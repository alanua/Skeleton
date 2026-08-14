from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StableDocument:
    path: Path
    source_id: str
    sha256: str
    size: int
    mtime_ns: int
    stable: bool


class StableFileGate:
    """Two-observation stability gate for local family-document intake."""

    def __init__(self) -> None:
        self._seen: dict[Path, tuple[int, int]] = {}

    def observe(self, path: Path) -> StableDocument:
        stat = path.stat()
        current = (int(stat.st_size), int(stat.st_mtime_ns))
        stable = self._seen.get(path) == current and current[0] > 0
        self._seen[path] = current
        digest = _sha256_file(path) if stable else ""
        return StableDocument(
            path=path,
            source_id=_source_id(path),
            sha256=digest,
            size=current[0],
            mtime_ns=current[1],
            stable=stable,
        )


class LocalDirectoryDocumentSource:
    def __init__(self, root: Path, *, suffixes: tuple[str, ...] = (".txt", ".pdf")) -> None:
        self.root = root
        self.suffixes = tuple(suffix.lower() for suffix in suffixes)
        self.gate = StableFileGate()

    def scan(self) -> list[StableDocument]:
        self.root.mkdir(parents=True, exist_ok=True)
        documents: list[StableDocument] = []
        for path in sorted(self.root.iterdir()):
            if not path.is_file() or path.suffix.lower() not in self.suffixes:
                continue
            observed = self.gate.observe(path)
            if observed.stable:
                documents.append(observed)
        return documents


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_id(path: Path) -> str:
    digest = hashlib.sha256(path.name.encode("utf-8", errors="ignore")).hexdigest()
    return f"local-{digest[:24]}"
