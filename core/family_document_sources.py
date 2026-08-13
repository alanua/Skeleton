from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from core.local_document_ocr import ALLOWED_EXTENSIONS


class FamilyDocumentSourceError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SourceInventoryItem:
    path: Path
    size: int
    mtime_ns: int
    extension: str


class ApprovedLocalSourceInventory:
    """Enumerates local inbox files without following unapproved roots."""

    def __init__(
        self,
        roots: tuple[str | Path, ...],
        *,
        allowed_extensions: tuple[str, ...] = ALLOWED_EXTENSIONS,
    ) -> None:
        if not roots:
            raise FamilyDocumentSourceError("source_roots_required", "at least one source root is required")
        self.roots = tuple(Path(root).expanduser().resolve() for root in roots)
        self.allowed_extensions = tuple(ext.lower() for ext in allowed_extensions)

    def iter_candidates(self) -> tuple[SourceInventoryItem, ...]:
        items: list[SourceInventoryItem] = []
        for root in self.roots:
            if not root.exists():
                continue
            if not root.is_dir():
                raise FamilyDocumentSourceError("source_root_not_directory", "source root is not a directory")
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                resolved = path.resolve()
                if not self.is_approved_path(resolved):
                    continue
                extension = resolved.suffix.lower()
                if extension not in self.allowed_extensions:
                    continue
                stat = resolved.stat()
                items.append(SourceInventoryItem(resolved, int(stat.st_size), int(stat.st_mtime_ns), extension))
        return tuple(items)

    def is_approved_path(self, path: str | Path) -> bool:
        resolved = Path(path).expanduser().resolve()
        return any(resolved == root or resolved.is_relative_to(root) for root in self.roots)


class StableFileGate:
    def __init__(self, *, min_age_seconds: float = 1.0) -> None:
        if min_age_seconds < 0:
            raise FamilyDocumentSourceError("stable_gate_age_invalid", "stable gate age must be non-negative")
        self.min_age_seconds = min_age_seconds

    def check(self, path: str | Path) -> tuple[bool, dict[str, object]]:
        source = Path(path)
        first = source.stat()
        now = time.time_ns()
        age_seconds = max(0.0, (now - int(first.st_mtime_ns)) / 1_000_000_000)
        if age_seconds < self.min_age_seconds:
            return False, {"reason": "FILE_TOO_NEW", "age_seconds": age_seconds}
        second = source.stat()
        stable = first.st_size == second.st_size and first.st_mtime_ns == second.st_mtime_ns
        return stable, {
            "reason": "STABLE" if stable else "FILE_CHANGED",
            "size": int(second.st_size),
            "mtime_ns": int(second.st_mtime_ns),
            "device": int(getattr(second, "st_dev", 0)),
            "inode": int(getattr(second, "st_ino", 0)),
        }


def approved_source_inventory_receipt(items: tuple[SourceInventoryItem, ...]) -> dict[str, object]:
    by_extension: dict[str, int] = {}
    for item in items:
        by_extension[item.extension] = by_extension.get(item.extension, 0) + 1
    return {
        "schema": "skeleton.family_document_source_inventory.v1",
        "privacy": "aggregate_only",
        "aggregate_counts": {"total": len(items), "by_extension": by_extension},
    }
