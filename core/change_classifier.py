from __future__ import annotations

from enum import Enum
from pathlib import PurePosixPath
from typing import Any


class ChangeDecision(Enum):
    SAFE = "SAFE"
    REVIEW = "REVIEW"
    STOP = "STOP"
    BLOCKED = "BLOCKED"


SAFE_TOP_LEVEL_DIRS = frozenset({"docs", "tests"})
SAFE_TOP_LEVEL_FILES = frozenset({"README.md"})
STOP_PATH_PARTS = frozenset(
    {
        ".github",
        ".ssh",
        "adapters",
        "scripts",
        "secrets",
    }
)
STOP_FILE_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        "id_rsa",
        "id_ed25519",
        "known_hosts",
    }
)
STOP_SUFFIXES = (".key", ".pem", ".p12", ".pfx")
STOP_SIGNAL_KEYS = frozenset(
    {
        "credential_change",
        "destructive_change",
        "network_change",
        "runtime_change",
        "secret_change",
    }
)


class ChangeClassifier:
    """Classify proposed repository changes without executing or applying them."""

    def check(self, change_data: dict[str, Any]) -> ChangeDecision:
        if not isinstance(change_data, dict):
            return ChangeDecision.BLOCKED

        files = _extract_files(change_data)
        if not files or any(not _is_safe_repo_path(path) for path in files):
            return ChangeDecision.BLOCKED

        if _has_stop_signal(change_data) or any(_is_stop_path(path) for path in files):
            return ChangeDecision.STOP

        if all(_is_safe_path(path) for path in files) and not _has_review_signal(change_data):
            return ChangeDecision.SAFE

        return ChangeDecision.REVIEW


def _extract_files(change_data: dict[str, Any]) -> tuple[str, ...]:
    for key in ("files", "changed_files", "paths"):
        value = change_data.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            return ()
        if not all(isinstance(item, str) for item in value):
            return ()
        return tuple(value)

    return ()


def _is_safe_repo_path(path: str) -> bool:
    if path.strip() != path or path == "" or "\\" in path:
        return False

    parsed = PurePosixPath(path)
    return not parsed.is_absolute() and all(part not in {"", ".", ".."} for part in parsed.parts)


def _is_stop_path(path: str) -> bool:
    parsed = PurePosixPath(path)
    name = parsed.name.lower()
    parts = frozenset(part.lower() for part in parsed.parts)

    return (
        bool(parts & STOP_PATH_PARTS)
        or name in STOP_FILE_NAMES
        or name.startswith(".env.")
        or name.endswith(STOP_SUFFIXES)
    )


def _is_safe_path(path: str) -> bool:
    parsed = PurePosixPath(path)
    if len(parsed.parts) == 1:
        return path in SAFE_TOP_LEVEL_FILES

    return parsed.parts[0] in SAFE_TOP_LEVEL_DIRS


def _has_stop_signal(change_data: dict[str, Any]) -> bool:
    return any(change_data.get(key) is True for key in STOP_SIGNAL_KEYS)


def _has_review_signal(change_data: dict[str, Any]) -> bool:
    return change_data.get("requires_review") is True or change_data.get("approval_required") is True
