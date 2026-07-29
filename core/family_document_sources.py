from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

SUPPORTED_SUFFIXES = frozenset(
    {".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".txt", ".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods"}
)
PARTIAL_SUFFIXES = (".part", ".partial", ".tmp", ".crdownload")
SKIP_DIRECTORIES = frozenset({".git", ".ssh", "secrets", "node_modules", "__pycache__"})


class SourceError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ApprovedRoot:
    alias: str
    path: Path

    def __post_init__(self) -> None:
        if not self.alias or "/" in self.alias or "\\" in self.alias:
            raise SourceError("invalid_root_alias")
        resolved = Path(self.path).expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise SourceError("approved_root_unavailable")
        if _has_symlink_component(resolved):
            raise SourceError("approved_root_symlinked")
        object.__setattr__(self, "path", resolved)


@dataclass(frozen=True)
class SourceReference:
    root_alias: str
    absolute_path: Path
    relative_path: str
    byte_size: int
    mtime_ns: int

    def private_dict(self) -> dict[str, object]:
        return {
            "root_alias": self.root_alias,
            "absolute_path": str(self.absolute_path),
            "relative_path": self.relative_path,
            "byte_size": self.byte_size,
            "mtime_ns": self.mtime_ns,
        }


def resolve_source(path: Path, roots: Sequence[ApprovedRoot]) -> SourceReference:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise SourceError("source_symlink_rejected")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise SourceError("source_unavailable") from exc
    if not resolved.is_file() or resolved.suffix.casefold() not in SUPPORTED_SUFFIXES:
        raise SourceError("source_unsupported")
    lowered_name = resolved.name.casefold()
    if lowered_name.endswith(PARTIAL_SUFFIXES):
        raise SourceError("source_partial")
    if _has_symlink_component(resolved):
        raise SourceError("source_symlink_rejected")
    matched: ApprovedRoot | None = None
    for root in roots:
        if resolved == root.path or root.path in resolved.parents:
            matched = root
            break
    if matched is None:
        raise SourceError("source_outside_approved_roots")
    stat = resolved.stat()
    if stat.st_size <= 0:
        raise SourceError("source_empty")
    return SourceReference(
        root_alias=matched.alias,
        absolute_path=resolved,
        relative_path=resolved.relative_to(matched.path).as_posix(),
        byte_size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def inventory_sources(
    roots: Sequence[ApprovedRoot],
    *,
    max_files: int = 10000,
) -> tuple[SourceReference, ...]:
    if isinstance(max_files, bool) or not isinstance(max_files, int) or not 1 <= max_files <= 100000:
        raise SourceError("inventory_limit_invalid")
    found: list[SourceReference] = []
    seen: set[Path] = set()
    for root in sorted(roots, key=lambda item: item.alias):
        for current, dirs, files in os.walk(root.path, followlinks=False):
            current_path = Path(current)
            dirs[:] = [
                name
                for name in sorted(dirs)
                if name.casefold() not in SKIP_DIRECTORIES
                and not (current_path / name).is_symlink()
            ]
            for name in sorted(files):
                candidate = current_path / name
                if candidate.suffix.casefold() not in SUPPORTED_SUFFIXES:
                    continue
                if name.casefold().endswith(PARTIAL_SUFFIXES):
                    continue
                try:
                    reference = resolve_source(candidate, roots)
                except SourceError:
                    continue
                if reference.absolute_path in seen:
                    continue
                seen.add(reference.absolute_path)
                found.append(reference)
                if len(found) >= max_files:
                    return tuple(found)
    return tuple(found)


def stable_observation(
    reference: SourceReference,
    previous: dict[str, object] | None,
    *,
    observed_at: float,
    settle_seconds: float,
) -> tuple[bool, dict[str, object]]:
    if settle_seconds < 0:
        raise SourceError("settle_seconds_invalid")
    current = {
        "byte_size": reference.byte_size,
        "mtime_ns": reference.mtime_ns,
        "observed_at": float(observed_at),
    }
    if not isinstance(previous, dict):
        return False, current
    unchanged = (
        previous.get("byte_size") == reference.byte_size
        and previous.get("mtime_ns") == reference.mtime_ns
    )
    prior_time = previous.get("observed_at")
    if isinstance(prior_time, bool) or not isinstance(prior_time, (int, float)):
        return False, current
    return bool(unchanged and observed_at - float(prior_time) >= settle_seconds), current


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False
