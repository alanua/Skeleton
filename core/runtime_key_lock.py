from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def runtime_key_lock(directory: str | Path, key: str) -> Iterator[None]:
    root = Path(directory).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    path = root / f".{key}.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with os.fdopen(descriptor, "r+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        if path.exists():
            path.chmod(0o600)
