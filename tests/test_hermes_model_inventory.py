from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from core.hermes_model_inventory import (
    HERMES_MODEL_ROUTE_REGISTRY_SCHEMA,
    HermesModelInventoryError,
    HermesModelInventoryStorageError,
    inventory_hermes_model_routes,
    persist_and_verify_hermes_model_inventory,
    public_hermes_model_inventory_report,
    read_detailed_hermes_model_inventory,
    save_detailed_hermes_model_inventory,
)
from core.private_memory import PRIVATE_MEMORY_CONFIG_SCHEMA


PRIVATE_PROVIDER = "private-provider-alpha"
PRIVATE_MODEL = "private/model-high"


def _route(
    route_id: str,
    *,
    aliases: list[str] | None = None,
    configured: bool = True,
    authenticated: bool = True,
    locally_reachable: bool = True,
    quota_known: bool = True,
    enabled: bool = True,
    capabilities: dict[str, bool] | None = None,
) -> dict[str, object]:
    return {
        "route_id": route_id,
        "provider": f"{PRIVATE_PROVIDER}-{route_id}",
        "model": f"{PRIVATE_MODEL}-{route_id}",
        "aliases": aliases if aliases is not None else [f"alias-{route_id}"],
        "configured": configured,
        "authenticated": authenticated,
        "locally_reachable": locally_reachable,
        "quota_known": quota_known,
        "enabled": enabled,
        "capabilities": capabilities
        if capabilities is not None
        else {"LOW": True, "MID": True, "HIGH": False},
    }


def _registry_path(tmp_path: Path, routes: list[dict[str, object]]) -> Path:
    path = tmp_path / "hermes_model_routes.private.json"
    path.write_text(
        json.dumps(
            {
                "schema": HERMES_MODEL_ROUTE_REGISTRY_SCHEMA,
                "routes": routes,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _private_memory_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "synthetic_config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema": PRIVATE_MEMORY_CONFIG_SCHEMA,
                "database": {"path": str(tmp_path / "memory.sqlite")},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _serialized_public_report(path: Path, config_path: Path) -> str:
    return json.dumps(
        public_hermes_model_inventory_report(
            registry_path=path,
            config_path=config_path,
        ),
        sort_keys=True,
    )


def test_inventory_separates_readiness_and_suitability_counts(tmp_path: Path) -> None:
    path = _registry_path(
        tmp_path,
        [
            _route(
                "ready",
                aliases=["fast", "balanced"],
                capabilities={"LOW": True, "MID": True, "HIGH": True},
            ),
            _route("auth_missing", authenticated=False),
            _route("unreachable", locally_reachable=False),
            _route("quota_unknown", quota_known=False),
        ],
    )
    config_path = _private_memory_config(tmp_path)

    report = public_hermes_model_inventory_report(
        registry_path=path,
        config_path=config_path,
    )

    assert report["status"] == "DONE"
    assert report["route_count"] == 4
    assert report["alias_count"] == 5
    assert report["configured_count"] == 4
    assert report["authenticated_count"] == 3
    assert report["locally_reachable_count"] == 3
    assert report["quota_known_count"] == 3
    assert report["enabled_count"] == 4
    assert report["low_capability_count"] == 4
    assert report["mid_capability_count"] == 4
    assert report["high_capability_count"] == 1
    assert report["low_suitability_count"] == 1
    assert report["mid_suitability_count"] == 1
    assert report["high_suitability_count"] == 1


def test_detailed_inventory_persists_and_reads_back_by_opaque_id(tmp_path: Path) -> None:
    path = _registry_path(tmp_path, [_route("ready")])
    config_path = _private_memory_config(tmp_path)
    inventory = inventory_hermes_model_routes(registry_path=path)

    inventory_id = persist_and_verify_hermes_model_inventory(
        inventory,
        config_path=config_path,
    )
    readback = read_detailed_hermes_model_inventory(
        inventory_id,
        config_path=config_path,
    )

    assert inventory_id == inventory.inventory_id
    assert readback == inventory.private_artifact()
    serialized = json.dumps(readback, sort_keys=True)
    assert PRIVATE_PROVIDER in serialized
    assert PRIVATE_MODEL in serialized


def test_detailed_inventory_save_is_idempotent_for_repeated_inventory(
    tmp_path: Path,
) -> None:
    path = _registry_path(tmp_path, [_route("ready")])
    config_path = _private_memory_config(tmp_path)
    inventory = inventory_hermes_model_routes(registry_path=path)

    first_id = save_detailed_hermes_model_inventory(inventory, config_path=config_path)
    second_id = save_detailed_hermes_model_inventory(inventory, config_path=config_path)

    assert first_id == second_id == inventory.inventory_id
    with sqlite3.connect(tmp_path / "memory.sqlite") as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM hermes_model_inventory_private WHERE inventory_id = ?",
            (inventory.inventory_id,),
        ).fetchone()
    assert row == (1,)


def test_public_report_redacts_provider_model_alias_and_path(tmp_path: Path) -> None:
    path = _registry_path(tmp_path, [_route("ready", aliases=["private_alias"])])
    config_path = _private_memory_config(tmp_path)

    serialized = _serialized_public_report(path, config_path)

    assert PRIVATE_PROVIDER not in serialized
    assert PRIVATE_MODEL not in serialized
    assert "private_alias" not in serialized
    assert str(tmp_path) not in serialized
    assert "hermes_model_routes.private.json" not in serialized
    assert "memory.sqlite" not in serialized


def test_duplicate_alias_fails_closed(tmp_path: Path) -> None:
    path = _registry_path(
        tmp_path,
        [
            _route("one", aliases=["shared"]),
            _route("two", aliases=["shared"]),
        ],
    )
    config_path = _private_memory_config(tmp_path)

    with pytest.raises(HermesModelInventoryError):
        inventory_hermes_model_routes(registry_path=path)

    report = public_hermes_model_inventory_report(
        registry_path=path,
        config_path=config_path,
    )
    assert report["status"] == "BLOCKED"
    assert report["route_count"] == 0


def test_malformed_private_metadata_fails_closed(tmp_path: Path) -> None:
    path = _registry_path(tmp_path, [_route("ready")])
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["routes"][0]["quota_known"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    config_path = _private_memory_config(tmp_path)

    with pytest.raises(HermesModelInventoryError):
        inventory_hermes_model_routes(registry_path=path)

    report = public_hermes_model_inventory_report(
        registry_path=path,
        config_path=config_path,
    )
    assert report["status"] == "BLOCKED"
    assert report["inventory_id"] == "unavailable"


def test_unsupported_private_metadata_field_fails_closed(tmp_path: Path) -> None:
    route = _route("ready")
    route["runtime_endpoint"] = "https://private-runtime.invalid"
    path = _registry_path(tmp_path, [route])
    config_path = _private_memory_config(tmp_path)

    with pytest.raises(HermesModelInventoryError):
        inventory_hermes_model_routes(registry_path=path)

    report = public_hermes_model_inventory_report(
        registry_path=path,
        config_path=config_path,
    )
    assert report["status"] == "BLOCKED"
    assert "runtime_endpoint" not in json.dumps(report, sort_keys=True)


def test_missing_registry_fails_closed_without_guessing_defaults(tmp_path: Path) -> None:
    report = public_hermes_model_inventory_report(
        registry_path=tmp_path / "missing.private.json",
        config_path=_private_memory_config(tmp_path),
    )

    assert report["status"] == "BLOCKED"
    assert report["route_count"] == 0
    assert report["enabled_count"] == 0
    assert report["low_suitability_count"] == 0


def test_missing_private_storage_fails_closed_even_when_registry_valid(
    tmp_path: Path,
) -> None:
    path = _registry_path(tmp_path, [_route("ready")])

    report = public_hermes_model_inventory_report(registry_path=path, env={})

    assert report["status"] == "BLOCKED"
    assert report["inventory_id"] == "unavailable"
    assert report["route_count"] == 0


def test_public_report_fails_closed_when_verified_readback_is_unavailable(
    tmp_path: Path,
) -> None:
    path = _registry_path(tmp_path, [_route("ready")])
    config_path = _private_memory_config(tmp_path)

    with mock.patch(
        "core.hermes_model_inventory.read_detailed_hermes_model_inventory",
        side_effect=HermesModelInventoryStorageError("private_inventory_read_failed"),
    ):
        report = public_hermes_model_inventory_report(
            registry_path=path,
            config_path=config_path,
        )

    assert report["status"] == "BLOCKED"
    assert report["inventory_id"] == "unavailable"
    assert report["route_count"] == 0
