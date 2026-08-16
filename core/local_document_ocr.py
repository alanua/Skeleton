from __future__ import annotations

import hashlib
import os
import re
import selectors
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


class LocalDocumentOcrError(RuntimeError):
    """Raised when a local document cannot be converted to bounded text."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class OcrResult:
    text: str
    page_count: int
    mime_type: str
    source_sha256: str
    providers: tuple[str, ...] = ()


@dataclass(frozen=True)
class OcrConfig:
    executables: Mapping[str, str]
    timeout_seconds: int = 300
    max_output_bytes: int = 2_000_000
    max_input_bytes: int = 100_000_000
    languages: tuple[str, ...] = ("eng", "deu", "ukr")

    def __post_init__(self) -> None:
        required = {"pdftotext", "pdfinfo", "ocrmypdf", "tesseract", "libreoffice"}
        if set(self.executables) != required:
            raise LocalDocumentOcrError("ocr_executable_set_invalid")
        for value in self.executables.values():
            if not isinstance(value, str) or not value or "\x00" in value or not Path(value).is_absolute():
                raise LocalDocumentOcrError("ocr_executable_invalid")
        if not 1 <= self.timeout_seconds <= 3600:
            raise LocalDocumentOcrError("ocr_timeout_invalid")
        if not 1024 <= self.max_output_bytes <= 20_000_000:
            raise LocalDocumentOcrError("ocr_output_limit_invalid")
        if not 1024 <= self.max_input_bytes <= 500_000_000:
            raise LocalDocumentOcrError("ocr_input_limit_invalid")


DEFAULT_OCR_CONFIG = OcrConfig(
    executables={
        "pdftotext": "/usr/bin/pdftotext",
        "pdfinfo": "/usr/bin/pdfinfo",
        "ocrmypdf": "/usr/bin/ocrmypdf",
        "tesseract": "/usr/bin/tesseract",
        "libreoffice": "/usr/bin/libreoffice",
    }
)


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
        raise LocalDocumentOcrError("ocr_executable_invalid")
    if any(not isinstance(value, str) or not value or "\x00" in value for value in argv):
        raise LocalDocumentOcrError("ocr_argument_invalid")
    if input_bytes is not None and len(input_bytes) > max_output:
        raise LocalDocumentOcrError("ocr_input_too_large")
    try:
        process = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "NO_COLOR": "1",
            },
        )
    except OSError as exc:
        raise LocalDocumentOcrError("ocr_command_failed") from exc
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
                raise LocalDocumentOcrError("ocr_timeout")
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
                    raise LocalDocumentOcrError("ocr_output_too_large")
        process.wait(timeout=1)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        raise LocalDocumentOcrError("ocr_command_failed") from exc
    finally:
        selector.close()
    return CommandResult(
        process.returncode,
        bytes(buffers[process.stdout.fileno()]),
        bytes(buffers[process.stderr.fileno()]),
    )


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


Runner = Callable[[Sequence[str], Path, int, int], CommandResult]


class LocalDocumentOcr:
    def __init__(self, config: OcrConfig = DEFAULT_OCR_CONFIG, runner: Runner | None = None) -> None:
        self.config = config
        self.runner = runner or self._run

    def extract(self, source: Path) -> OcrResult:
        try:
            source = Path(source).resolve(strict=True)
            stat = source.stat()
        except OSError as exc:
            raise LocalDocumentOcrError("ocr_source_unavailable") from exc
        if not source.is_file() or source.is_symlink():
            raise LocalDocumentOcrError("ocr_source_invalid")
        if stat.st_size <= 0:
            raise LocalDocumentOcrError("document_empty")
        if stat.st_size > self.config.max_input_bytes:
            raise LocalDocumentOcrError("document_too_large")

        suffix = source.suffix.casefold()
        source_hash = sha256_file(source)
        if suffix == ".txt":
            try:
                raw = source.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise LocalDocumentOcrError("text_read_failed") from exc
            return self._result(raw, 1, "text/plain", source_hash, ("utf8_text",))
        if suffix == ".pdf":
            return self._extract_pdf(source, source_hash)
        if suffix in {".tif", ".tiff", ".png", ".jpg", ".jpeg"}:
            text = self._tesseract(source)
            mime = {
                ".tif": "image/tiff",
                ".tiff": "image/tiff",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
            }[suffix]
            return self._result(text, 1, mime, source_hash, ("tesseract",))
        if suffix in {".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods"}:
            return self._extract_office(source, source_hash)
        raise LocalDocumentOcrError("ocr_format_unsupported")

    def _extract_pdf(self, source: Path, source_hash: str) -> OcrResult:
        page_count = self._pdf_page_count(source)
        text = self._pdftotext(source)
        if text.strip():
            return self._result(text, page_count, "application/pdf", source_hash, ("pdftotext",))
        with tempfile.TemporaryDirectory(prefix="family-doc-ocr-") as temporary_dir:
            output = Path(temporary_dir) / "searchable.pdf"
            args = (
                self.config.executables["ocrmypdf"],
                "--skip-text",
                "--deskew",
                "--rotate-pages",
                "--language",
                "+".join(self.config.languages),
                str(source),
                str(output),
            )
            result = self.runner(args, Path(temporary_dir), self.config.timeout_seconds, self.config.max_output_bytes)
            if result.returncode != 0 or not output.is_file():
                raise LocalDocumentOcrError("pdf_ocr_failed")
            text = self._pdftotext(output)
            if not text.strip():
                raise LocalDocumentOcrError("pdf_ocr_empty")
            return self._result(
                text,
                page_count,
                "application/pdf",
                source_hash,
                ("ocrmypdf", "pdftotext"),
            )

    def _extract_office(self, source: Path, source_hash: str) -> OcrResult:
        with tempfile.TemporaryDirectory(prefix="family-doc-office-") as temporary_dir:
            workspace = Path(temporary_dir)
            args = (
                self.config.executables["libreoffice"],
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(workspace),
                str(source),
            )
            result = self.runner(args, workspace, self.config.timeout_seconds, self.config.max_output_bytes)
            if result.returncode != 0:
                raise LocalDocumentOcrError("office_conversion_failed")
            candidates = tuple(workspace.glob("*.pdf"))
            if len(candidates) != 1:
                raise LocalDocumentOcrError("office_conversion_output_invalid")
            converted = self._extract_pdf(candidates[0], source_hash)
            return OcrResult(
                text=converted.text,
                page_count=converted.page_count,
                mime_type="application/pdf",
                source_sha256=source_hash,
                providers=("libreoffice", *converted.providers),
            )

    def _pdf_page_count(self, source: Path) -> int:
        result = self.runner(
            (self.config.executables["pdfinfo"], str(source)),
            source.parent,
            self.config.timeout_seconds,
            self.config.max_output_bytes,
        )
        if result.returncode != 0:
            raise LocalDocumentOcrError("pdfinfo_failed")
        match = re.search(r"(?mi)^Pages:\s*(\d+)\s*$", result.stdout.decode("utf-8", errors="replace"))
        if match is None:
            raise LocalDocumentOcrError("pdf_page_count_missing")
        page_count = int(match.group(1))
        if not 1 <= page_count <= 10000:
            raise LocalDocumentOcrError("pdf_page_count_invalid")
        return page_count

    def _pdftotext(self, source: Path) -> str:
        result = self.runner(
            (self.config.executables["pdftotext"], "-layout", str(source), "-"),
            source.parent,
            self.config.timeout_seconds,
            self.config.max_output_bytes,
        )
        if result.returncode != 0:
            raise LocalDocumentOcrError("pdftotext_failed")
        return result.stdout.decode("utf-8", errors="replace")[: self.config.max_output_bytes]

    def _tesseract(self, source: Path) -> str:
        result = self.runner(
            (
                self.config.executables["tesseract"],
                str(source),
                "stdout",
                "-l",
                "+".join(self.config.languages),
                "--psm",
                "6",
            ),
            source.parent,
            self.config.timeout_seconds,
            self.config.max_output_bytes,
        )
        if result.returncode != 0:
            raise LocalDocumentOcrError("tesseract_failed")
        return result.stdout.decode("utf-8", errors="replace")[: self.config.max_output_bytes]

    def _result(
        self,
        text: str,
        page_count: int,
        mime_type: str,
        source_sha256: str,
        providers: tuple[str, ...],
    ) -> OcrResult:
        normalized = "\n".join(
            line.rstrip()
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        ).strip()
        if not normalized:
            raise LocalDocumentOcrError("ocr_empty")
        return OcrResult(
            text=normalized[: self.config.max_output_bytes],
            page_count=page_count,
            mime_type=mime_type,
            source_sha256=source_sha256,
            providers=providers,
        )

    @staticmethod
    def _run(argv: Sequence[str], cwd: Path, timeout: int, max_output: int) -> CommandResult:
        return run_bounded_argv(argv, cwd, timeout, max_output)


def read_local_document(path: Path, *, ocr: LocalDocumentOcr | None = None) -> OcrResult:
    return (ocr or LocalDocumentOcr()).extract(path)


def read_local_document_text(path: Path, *, max_bytes: int = 2_000_000) -> str:
    config = OcrConfig(
        executables=DEFAULT_OCR_CONFIG.executables,
        timeout_seconds=DEFAULT_OCR_CONFIG.timeout_seconds,
        max_output_bytes=max_bytes,
        max_input_bytes=DEFAULT_OCR_CONFIG.max_input_bytes,
        languages=DEFAULT_OCR_CONFIG.languages,
    )
    return LocalDocumentOcr(config).extract(path).text


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
