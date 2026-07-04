from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.runtime_context_loader import (
    RuntimeContextError,
    load_runtime_context,
)


def write_private_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def test_load_runtime_context_from_private_registries(tmp_path: Path) -> None:
    targets = tmp_path / "targets.json"
    roots = tmp_path / "roots.json"
    data_root = tmp_path / "data"
    write_private_json(
        targets,
        {
            "home-edge": {
                "host": "example.invalid",
                "user": "operator",
                "identity_file": "/private/key",
                "known_hosts_file": "/private/known_hosts",
            }
        },
    )
    write_private_json(roots, {"data": str(data_root)})

    context = load_runtime_context(
        target_registry_path=targets,
        root_registry_path=roots,
    )

    assert context.targets["home-edge"]["user"] == "operator"
    assert context.roots["data"] == data_root.resolve()
    assert context.environment == {}


def test_registry_with_broad_permissions_is_rejected(tmp_path: Path) -> None:
    targets = tmp_path / "targets.json"
    targets.write_text("{}", encoding="utf-8")
    targets.chmod(0o644)

    with pytest.raises(RuntimeContextError, match="owner-only"):
        load_runtime_context(target_registry_path=targets)
