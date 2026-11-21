from __future__ import annotations

import hashlib
import os
import stat as stat_module
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
    snapshot: tuple[int, ...] = ()


class StableFileGate:
    """Two-observation stability gate for local family-document intake."""

    def __init__(self) -> None:
        self._seen: dict[Path, tuple[int, ...]] = {}

    def observe(self, path: Path) -> StableDocument:
        stat = path.stat()
        current = snapshot_signature(stat)
        stable = self._seen.get(path) == current and stat.st_size > 0
        self._seen[path] = current
        digest = _sha256_file(path) if stable else ""
        if stable and snapshot_signature(path.stat()) != current:
            self._seen.pop(path, None)
            raise ValueError("source snapshot changed")
        return StableDocument(
            path=path,
            source_id=_source_id(path),
            sha256=digest,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            stable=stable,
            snapshot=current,
        )


class LocalDirectoryDocumentSource:
    def __init__(
        self,
        root: Path,
        *,
        suffixes: tuple[str, ...] = (".txt", ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".doc", ".docx", ".odt", ".rtf"),
    ) -> None:
        self.root = root
        self.suffixes = tuple(suffix.lower() for suffix in suffixes)
        self.gate = StableFileGate()

    def scan(self) -> list[StableDocument]:
        self.root.mkdir(parents=True, exist_ok=True)
        documents: list[StableDocument] = []
        for path in sorted(self.root.iterdir()):
            if not path.is_file() or path.is_symlink() or path.suffix.lower() not in self.suffixes:
                continue
            try:
                observed = self.gate.observe(path)
            except (OSError, ValueError):
                self.gate._seen.pop(path, None)
                continue
            if observed.stable:
                documents.append(observed)
        return documents


def snapshot_signature(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def read_snapshot(path: Path, expected: tuple[int, ...] = ()) -> bytes:
    """Read a single regular-file inode, rejecting replacement and concurrent writes."""
    before = path.lstat()
    signature = snapshot_signature(before)
    if not stat_module.S_ISREG(before.st_mode) or (expected and signature != expected):
        raise ValueError("source snapshot changed")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as handle:
        if snapshot_signature(os.fstat(handle.fileno())) != signature:
            raise ValueError("source snapshot changed")
        data = handle.read(50_000_001)
        if len(data) > 50_000_000:
            raise ValueError("source exceeds byte limit")
        if snapshot_signature(os.fstat(handle.fileno())) != signature:
            raise ValueError("source snapshot changed")
    if snapshot_signature(path.lstat()) != signature or len(data) != before.st_size:
        raise ValueError("source snapshot changed")
    return data


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(read_snapshot(path)).hexdigest()


def _source_id(path: Path) -> str:
    digest = hashlib.sha256(path.name.encode("utf-8", errors="ignore")).hexdigest()
    return f"local-{digest[:24]}"