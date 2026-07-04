from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RuntimeReceiptCacheError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeReceiptCache:
    root: Path

    @classmethod
    def open(cls, root: str | Path) -> "RuntimeReceiptCache":
        path = Path(root).expanduser().resolve(strict=False)
        if not path.is_absolute():
            raise RuntimeReceiptCacheError("cache directory must be absolute")
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
        return cls(path)

    def read(self, key: str) -> dict[str, Any] | None:
        path = self.root / f"{key}.json"
        if not path.is_file():
            return None
        if path.stat().st_mode & 0o077:
            raise RuntimeReceiptCacheError("cache file must be owner-only")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeReceiptCacheError("cache JSON must be an object")
        return value
