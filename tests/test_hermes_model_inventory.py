from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.hermes_model_inventory import (
    HERMES_MODEL_ROUTE_REGISTRY_SCHEMA,
    HermesModelInventoryError,
    inventory_hermes_model_routes,
    public_hermes_model_inventory_report,
)


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


def _serialized_public_report(path: Path) -> str:
    return json.dumps(
        public_hermes_model_inventory_report(registry_path=path),
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

    report = public_hermes_model_inventory_report(registry_path=path)

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


def test_private_artifact_keeps_concrete_provider_and_model_names(tmp_path: Path) -> None:
    path = _registry_path(tmp_path, [_route("ready")])

    artifact = inventory_hermes_model_routes(registry_path=path).private_artifact()

    serialized = json.dumps(artifact, sort_keys=True)
    assert PRIVATE_PROVIDER in serialized
    assert PRIVATE_MODEL in serialized


def test_public_report_redacts_provider_model_alias_and_path(tmp_path: Path) -> None:
    path = _registry_path(tmp_path, [_route("ready", aliases=["private_alias"])])

    serialized = _serialized_public_report(path)

    assert PRIVATE_PROVIDER not in serialized
    assert PRIVATE_MODEL not in serialized
    assert "private_alias" not in serialized
    assert str(tmp_path) not in serialized
    assert "hermes_model_routes.private.json" not in serialized


def test_duplicate_alias_fails_closed(tmp_path: Path) -> None:
    path = _registry_path(
        tmp_path,
        [
            _route("one", aliases=["shared"]),
            _route("two", aliases=["shared"]),
        ],
    )

    with pytest.raises(HermesModelInventoryError):
        inventory_hermes_model_routes(registry_path=path)

    report = public_hermes_model_inventory_report(registry_path=path)
    assert report["status"] == "BLOCKED"
    assert report["route_count"] == 0


def test_malformed_private_metadata_fails_closed(tmp_path: Path) -> None:
    path = _registry_path(tmp_path, [_route("ready")])
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["routes"][0]["quota_known"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(HermesModelInventoryError):
        inventory_hermes_model_routes(registry_path=path)

    report = public_hermes_model_inventory_report(registry_path=path)
    assert report["status"] == "BLOCKED"
    assert report["inventory_id"] == "unavailable"


def test_unsupported_private_metadata_field_fails_closed(tmp_path: Path) -> None:
    route = _route("ready")
    route["runtime_endpoint"] = "https://private-runtime.invalid"
    path = _registry_path(tmp_path, [route])

    with pytest.raises(HermesModelInventoryError):
        inventory_hermes_model_routes(registry_path=path)

    report = public_hermes_model_inventory_report(registry_path=path)
    assert report["status"] == "BLOCKED"
    assert "runtime_endpoint" not in json.dumps(report, sort_keys=True)


def test_missing_registry_fails_closed_without_guessing_defaults(tmp_path: Path) -> None:
    report = public_hermes_model_inventory_report(
        registry_path=tmp_path / "missing.private.json"
    )

    assert report["status"] == "BLOCKED"
    assert report["route_count"] == 0
    assert report["enabled_count"] == 0
    assert report["low_suitability_count"] == 0
