from __future__ import annotations

import hashlib
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


class OcrError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class OcrConfig:
    executables: Mapping[str, str]
    timeout_seconds: int = 300
    max_output_bytes: int = 2_000_000
    languages: tuple[str, ...] = ("eng", "deu", "ukr")

    def __post_init__(self) -> None:
        required = {"pdftotext", "ocrmypdf", "tesseract", "libreoffice"}
        if set(self.executables) != required:
            raise OcrError("ocr_executable_set_invalid")
        normalized: dict[str, str] = {}
        for key, value in self.executables.items():
            path = Path(value).expanduser()
            if not path.is_absolute() or "\x00" in str(value):
                raise OcrError("ocr_executable_invalid")
            normalized[key] = str(path)
        object.__setattr__(self, "executables", normalized)
        if not 1 <= self.timeout_seconds <= 3600:
            raise OcrError("ocr_timeout_invalid")
        if not 1024 <= self.max_output_bytes <= 20_000_000:
            raise OcrError("ocr_output_limit_invalid")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def run_bounded_argv(
    argv: Sequence[str],
    cwd: Path,
    timeout: int,
    max_output: int,
    *,
    input_bytes: bytes | None = None,
) -> CommandResult:
    if not argv or not Path(argv[0]).is_absolute():
        raise OcrError("ocr_executable_invalid")
    if any(not isinstance(value, str) or not value or "\x00" in value for value in argv):
        raise OcrError("ocr_argument_invalid")
    if input_bytes is not None and len(input_bytes) > max_output:
        raise OcrError("ocr_input_too_large")
    try:
        process = subprocess.Popen(
            list(argv), cwd=str(cwd),
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            start_new_session=True,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "NO_COLOR": "1"},
        )
    except OSError as exc:
        raise OcrError("ocr_command_failed") from exc
    if input_bytes is not None:
        assert process.stdin is not None
        try:
            process.stdin.write(input_bytes)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    buffers = {process.stdout.fileno(): bytearray(), process.stderr.fileno(): bytearray()}
    streams = {process.stdout.fileno(): process.stdout, process.stderr.fileno(): process.stderr}
    started = time.monotonic()
    try:
        for fd, stream in streams.items():
            os.set_blocking(fd, False)
            selector.register(stream, selectors.EVENT_READ, fd)
        while selector.get_map() or process.poll() is None:
            if time.monotonic() - started > timeout:
                _terminate_process_group(process)
                raise OcrError("ocr_timeout")
            events = selector.select(0.1)
            if not events and process.poll() is not None:
                events = [(key, selectors.EVENT_READ) for key in list(selector.get_map().values())]
            for key, _ in events:
                fd = int(key.data)
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    try:
                        selector.unregister(key.fileobj)
                    except KeyError:
                        pass
                    continue
                buffers[fd].extend(chunk)
                if sum(len(value) for value in buffers.values()) > max_output:
                    _terminate_process_group(process)
                    raise OcrError("ocr_output_too_large")
        process.wait(timeout=1)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        raise OcrError("ocr_command_failed") from exc
    finally:
        selector.close()
    return CommandResult(process.returncode, bytes(buffers[process.stdout.fileno()]), bytes(buffers[process.stderr.fileno()]))


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


@dataclass(frozen=True)
class OcrResult:
    raw_text: str
    corrected_text: str
    providers: tuple[str, ...]
    source_sha256: str
    raw_text_sha256: str
    corrected_text_sha256: str

    def private_dict(self) -> dict[str, object]:
        return {
            "raw_text": self.raw_text,
            "corrected_text": self.corrected_text,
            "providers": list(self.providers),
            "source_sha256": self.source_sha256,
            "raw_text_sha256": self.raw_text_sha256,
            "corrected_text_sha256": self.corrected_text_sha256,
        }


Runner = Callable[[Sequence[str], Path, int, int], CommandResult]


class LocalDocumentOcr:
    def __init__(self, config: OcrConfig, runner: Runner | None = None) -> None:
        self.config = config
        self.runner = runner or self._run

    def validate_providers(self) -> dict[str, bool]:
        return {key: Path(value).is_file() and os.access(value, os.X_OK) for key, value in self.config.executables.items()}

    def extract(self, source: Path) -> OcrResult:
        source = Path(source).resolve(strict=True)
        suffix = source.suffix.casefold()
        source_hash = sha256_file(source)
        providers: list[str] = []
        if suffix == ".txt":
            try:
                raw = source.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise OcrError("text_read_failed") from exc
            providers.append("utf8_text")
        elif suffix == ".pdf":
            raw, used = self._extract_pdf(source)
            providers.extend(used)
        elif suffix in {".tif", ".tiff", ".png", ".jpg", ".jpeg"}:
            raw = self._tesseract(source)
            providers.append("tesseract")
        elif suffix in {".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods"}:
            raw, used = self._extract_office(source)
            providers.extend(used)
        else:
            raise OcrError("ocr_format_unsupported")
        raw = raw[: self.config.max_output_bytes].strip()
        if not raw:
            raise OcrError("ocr_empty")
        corrected = "\n".join(line.rstrip() for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()
        return OcrResult(raw, corrected, tuple(providers), source_hash, hashlib.sha256(raw.encode("utf-8")).hexdigest(), hashlib.sha256(corrected.encode("utf-8")).hexdigest())

    def _extract_pdf(self, source: Path) -> tuple[str, tuple[str, ...]]:
        text = self._pdftotext(source)
        if text.strip():
            return text, ("pdftotext",)
        with tempfile.TemporaryDirectory(prefix="family-doc-ocr-") as temporary_dir:
            output = Path(temporary_dir) / "ocr.pdf"
            args = (self.config.executables["ocrmypdf"], "--skip-text", "--deskew", "--rotate-pages", "--language", "+".join(self.config.languages), str(source), str(output))
            result = self.runner(args, Path(temporary_dir), self.config.timeout_seconds, self.config.max_output_bytes)
            if result.returncode != 0 or not output.is_file():
                raise OcrError("pdf_ocr_failed")
            text = self._pdftotext(output)
            if not text.strip():
                raise OcrError("pdf_ocr_empty")
            return text, ("ocrmypdf", "pdftotext")

    def _extract_office(self, source: Path) -> tuple[str, tuple[str, ...]]:
        with tempfile.TemporaryDirectory(prefix="family-doc-office-") as temporary_dir:
            workspace = Path(temporary_dir)
            args = (self.config.executables["libreoffice"], "--headless", "--convert-to", "pdf", "--outdir", str(workspace), str(source))
            result = self.runner(args, workspace, self.config.timeout_seconds, self.config.max_output_bytes)
            if result.returncode != 0:
                raise OcrError("office_conversion_failed")
            candidates = tuple(workspace.glob("*.pdf"))
            if len(candidates) != 1:
                raise OcrError("office_conversion_output_invalid")
            text, used = self._extract_pdf(candidates[0])
            return text, ("libreoffice", *used)

    def _pdftotext(self, source: Path) -> str:
        result = self.runner((self.config.executables["pdftotext"], "-layout", str(source), "-"), source.parent, self.config.timeout_seconds, self.config.max_output_bytes)
        if result.returncode != 0:
            raise OcrError("pdftotext_failed")
        return result.stdout.decode("utf-8", errors="replace")[: self.config.max_output_bytes]

    def _tesseract(self, source: Path) -> str:
        args = (self.config.executables["tesseract"], str(source), "stdout", "-l", "+".join(self.config.languages), "--psm", "6")
        result = self.runner(args, source.parent, self.config.timeout_seconds, self.config.max_output_bytes)
        if result.returncode != 0:
            raise OcrError("tesseract_failed")
        return result.stdout.decode("utf-8", errors="replace")[: self.config.max_output_bytes]

    @staticmethod
    def _run(argv: Sequence[str], cwd: Path, timeout: int, max_output: int) -> CommandResult:
        return run_bounded_argv(argv, cwd, timeout, max_output)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
