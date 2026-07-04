from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


class PrivateJsonStoreError(ValueError):
    pass


def write_private_json(
    directory: str | Path,
    filename: str,
    value: Mapping[str, Any],
) -> Path:
    root = Path(directory).expanduser().resolve(strict=False)
    if not root.is_absolute():
        raise PrivateJsonStoreError("directory must be absolute")
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    destination = root / filename
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, destination)
    destination.chmod(0o600)
    return destination
