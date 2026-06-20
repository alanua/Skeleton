from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from core.private_memory import PrivateMemoryConnector


HERMES_MODEL_REGISTRY_ENV = "SKELETON_HERMES_MODEL_ROUTE_REGISTRY"
HERMES_MODEL_ROUTE_REGISTRY_SCHEMA = "skeleton.hermes_model_routes.private.v1"
HERMES_MODEL_INVENTORY_PUBLIC_SCHEMA = "skeleton.hermes_model_inventory.public.v1"
HERMES_MODEL_INVENTORY_PRIVATE_SCHEMA = "skeleton.hermes_model_inventory.private.v1"
MODEL_TIERS = ("LOW", "MID", "HIGH")

_PRIVATE_INVENTORY_TABLE = "hermes_model_inventory_private"
_REGISTRY_KEYS = frozenset({"schema", "routes"})
_ROUTE_KEYS = frozenset(
    {
        "route_id",
        "provider",
        "model",
        "aliases",
        "configured",
        "authenticated",
        "locally_reachable",
        "quota_known",
        "enabled",
        "capabilities",
    }
)
_CAPABILITY_KEYS = frozenset(MODEL_TIERS)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_PRIVATE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,191}$")


class HermesModelInventoryError(Exception):
    """Raised when private Hermes model metadata is missing or unsafe."""


class HermesModelInventoryStorageError(HermesModelInventoryError):
    """Raised when private inventory persistence or readback fails."""


@dataclass(frozen=True)
class HermesModelRoute:
    route_id: str
    provider: str
    model: str
    aliases: tuple[str, ...]
    configured: bool
    authenticated: bool
    locally_reachable: bool
    quota_known: bool
    enabled: bool
    capabilities: Mapping[str, bool]

    @property
    def ready(self) -> bool:
        return (
            self.configured
            and self.authenticated
            and self.locally_reachable
            and self.quota_known
            and self.enabled
        )


@dataclass(frozen=True)
class HermesModelInventory:
    schema: str
    inventory_id: str
    routes: tuple[HermesModelRoute, ...]

    def private_artifact(self) -> dict[str, object]:
        return {
            "schema": HERMES_MODEL_INVENTORY_PRIVATE_SCHEMA,
            "inventory_id": self.inventory_id,
            "routes": [_route_to_private_artifact(route) for route in self.routes],
        }

    def public_report(self) -> dict[str, object]:
        readiness_fields = (
            "configured",
            "authenticated",
            "locally_reachable",
            "quota_known",
            "enabled",
        )
        report: dict[str, object] = {
            "schema": HERMES_MODEL_INVENTORY_PUBLIC_SCHEMA,
            "status": "DONE",
            "inventory_id": self.inventory_id,
            "route_count": len(self.routes),
            "alias_count": sum(len(route.aliases) for route in self.routes),
        }
        for field in readiness_fields:
            report[f"{field}_count"] = sum(
                1 for route in self.routes if getattr(route, field) is True
            )
        for tier in MODEL_TIERS:
            tier_key = tier.lower()
            report[f"{tier_key}_capability_count"] = sum(
                1 for route in self.routes if route.capabilities[tier] is True
            )
            report[f"{tier_key}_suitability_count"] = sum(
                1
                for route in self.routes
                if route.ready and route.capabilities[tier] is True
            )
        return report


def inventory_hermes_model_routes(
    *,
    registry_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> HermesModelInventory:
    path = _registry_path(registry_path=registry_path, env=env)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HermesModelInventoryError("private_registry_unreadable") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HermesModelInventoryError("private_registry_malformed") from exc
    if not isinstance(payload, dict):
        raise HermesModelInventoryError("private_registry_malformed")
    _reject_unknown_keys(payload, _REGISTRY_KEYS)
    if payload.get("schema") != HERMES_MODEL_ROUTE_REGISTRY_SCHEMA:
        raise HermesModelInventoryError("private_registry_malformed")
    raw_routes = payload.get("routes")
    if not isinstance(raw_routes, list) or not raw_routes:
        raise HermesModelInventoryError("private_registry_malformed")

    routes: list[HermesModelRoute] = []
    route_ids: set[str] = set()
    aliases: set[str] = set()
    for raw_route in raw_routes:
        route = _parse_route(raw_route)
        if route.route_id in route_ids:
            raise HermesModelInventoryError("private_registry_ambiguous")
        duplicate_aliases = aliases.intersection(route.aliases)
        if duplicate_aliases:
            raise HermesModelInventoryError("private_registry_ambiguous")
        route_ids.add(route.route_id)
        aliases.update(route.aliases)
        routes.append(route)

    inventory_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return HermesModelInventory(
        schema=HERMES_MODEL_INVENTORY_PRIVATE_SCHEMA,
        inventory_id=inventory_id,
        routes=tuple(routes),
    )


def save_detailed_hermes_model_inventory(
    inventory: HermesModelInventory,
    *,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    if not isinstance(inventory, HermesModelInventory):
        raise TypeError("inventory must be a HermesModelInventory.")
    artifact = inventory.private_artifact()
    payload_json = _canonical_json(artifact)
    connector = PrivateMemoryConnector(config_path=config_path, env=env)
    try:
        db_path = connector._load_db_path()
        with closing(connector._connect(db_path, write=True)) as connection:
            connector._ensure_schema(connection)
            with connection:
                connection.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {_PRIVATE_INVENTORY_TABLE} (
                        inventory_id TEXT PRIMARY KEY,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                connection.execute(
                    f"""
                    INSERT INTO {_PRIVATE_INVENTORY_TABLE}
                        (inventory_id, payload_json, created_at, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(inventory_id) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        updated_at = excluded.updated_at
                    """,
                    (inventory.inventory_id, payload_json),
                )
    except Exception as exc:  # noqa: BLE001 - callers must fail closed.
        raise HermesModelInventoryStorageError("private_inventory_save_failed") from exc
    return inventory.inventory_id


def read_detailed_hermes_model_inventory(
    inventory_id: str,
    *,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    inventory_id = _required_safe_id(inventory_id, "inventory_id")
    connector = PrivateMemoryConnector(config_path=config_path, env=env)
    try:
        db_path = connector._load_db_path()
        with closing(connector._connect(db_path, write=False)) as connection:
            row = connection.execute(
                f"""
                SELECT payload_json
                FROM {_PRIVATE_INVENTORY_TABLE}
                WHERE inventory_id = ?
                """,
                (inventory_id,),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 - callers must fail closed.
        raise HermesModelInventoryStorageError("private_inventory_read_failed") from exc
    if row is None:
        raise HermesModelInventoryStorageError("private_inventory_missing")
    try:
        payload = json.loads(str(row["payload_json"]))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HermesModelInventoryStorageError("private_inventory_malformed") from exc
    _validate_private_artifact(payload, expected_inventory_id=inventory_id)
    return payload


def persist_and_verify_hermes_model_inventory(
    inventory: HermesModelInventory,
    *,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    inventory_id = save_detailed_hermes_model_inventory(
        inventory,
        config_path=config_path,
        env=env,
    )
    readback = read_detailed_hermes_model_inventory(
        inventory_id,
        config_path=config_path,
        env=env,
    )
    if _canonical_json(readback) != _canonical_json(inventory.private_artifact()):
        raise HermesModelInventoryStorageError("private_inventory_readback_mismatch")
    return inventory_id


def public_hermes_model_inventory_report(
    *,
    registry_path: str | Path | None = None,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    try:
        inventory = inventory_hermes_model_routes(
            registry_path=registry_path,
            env=env,
        )
        persist_and_verify_hermes_model_inventory(
            inventory,
            config_path=config_path,
            env=env,
        )
    except HermesModelInventoryError:
        return _blocked_public_report()
    return inventory.public_report()


def _registry_path(
    *,
    registry_path: str | Path | None,
    env: Mapping[str, str] | None,
) -> Path:
    if registry_path is not None:
        candidate = Path(registry_path)
    else:
        source = os.environ if env is None else env
        raw_path = source.get(HERMES_MODEL_REGISTRY_ENV)
        if raw_path is None or not raw_path.strip():
            raise HermesModelInventoryError("private_registry_missing")
        candidate = Path(raw_path)
    if not candidate.is_file():
        raise HermesModelInventoryError("private_registry_missing")
    return candidate


def _parse_route(raw_route: object) -> HermesModelRoute:
    if not isinstance(raw_route, dict):
        raise HermesModelInventoryError("private_registry_malformed")
    _reject_unknown_keys(raw_route, _ROUTE_KEYS)
    route_id = _required_safe_id(raw_route.get("route_id"), "route_id")
    provider = _required_private_name(raw_route.get("provider"), "provider")
    model = _required_private_name(raw_route.get("model"), "model")
    aliases = _required_aliases(raw_route.get("aliases"))
    capabilities = _required_capabilities(raw_route.get("capabilities"))
    return HermesModelRoute(
        route_id=route_id,
        provider=provider,
        model=model,
        aliases=aliases,
        configured=_required_bool(raw_route.get("configured"), "configured"),
        authenticated=_required_bool(raw_route.get("authenticated"), "authenticated"),
        locally_reachable=_required_bool(
            raw_route.get("locally_reachable"),
            "locally_reachable",
        ),
        quota_known=_required_bool(raw_route.get("quota_known"), "quota_known"),
        enabled=_required_bool(raw_route.get("enabled"), "enabled"),
        capabilities=capabilities,
    )


def _blocked_public_report() -> dict[str, object]:
    report: dict[str, object] = {
        "schema": HERMES_MODEL_INVENTORY_PUBLIC_SCHEMA,
        "status": "BLOCKED",
        "inventory_id": "unavailable",
        "route_count": 0,
        "alias_count": 0,
        "configured_count": 0,
        "authenticated_count": 0,
        "locally_reachable_count": 0,
        "quota_known_count": 0,
        "enabled_count": 0,
    }
    for tier in MODEL_TIERS:
        tier_key = tier.lower()
        report[f"{tier_key}_capability_count"] = 0
        report[f"{tier_key}_suitability_count"] = 0
    return report


def _reject_unknown_keys(data: Mapping[str, object], allowed: frozenset[str]) -> None:
    if set(data) - allowed:
        raise HermesModelInventoryError("private_registry_unsupported_field")


def _required_bool(value: object, field: str) -> bool:
    del field
    if isinstance(value, bool):
        return value
    raise HermesModelInventoryError("private_registry_malformed")


def _required_safe_id(value: object, field: str) -> str:
    del field
    if isinstance(value, str) and _SAFE_ID_RE.fullmatch(value):
        return value
    raise HermesModelInventoryError("private_registry_malformed")


def _required_private_name(value: object, field: str) -> str:
    del field
    if isinstance(value, str) and _SAFE_PRIVATE_NAME_RE.fullmatch(value):
        return value
    raise HermesModelInventoryError("private_registry_malformed")


def _required_aliases(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise HermesModelInventoryError("private_registry_malformed")
    aliases = tuple(_required_safe_id(alias, "alias") for alias in value)
    if len(set(aliases)) != len(aliases):
        raise HermesModelInventoryError("private_registry_ambiguous")
    return aliases


def _required_capabilities(value: object) -> dict[str, bool]:
    if not isinstance(value, dict):
        raise HermesModelInventoryError("private_registry_malformed")
    _reject_unknown_keys(value, _CAPABILITY_KEYS)
    if set(value) != _CAPABILITY_KEYS:
        raise HermesModelInventoryError("private_registry_malformed")
    return {tier: _required_bool(value[tier], tier) for tier in MODEL_TIERS}


def _validate_private_artifact(
    payload: object,
    *,
    expected_inventory_id: str,
) -> None:
    if not isinstance(payload, dict):
        raise HermesModelInventoryStorageError("private_inventory_malformed")
    if payload.get("schema") != HERMES_MODEL_INVENTORY_PRIVATE_SCHEMA:
        raise HermesModelInventoryStorageError("private_inventory_malformed")
    if payload.get("inventory_id") != expected_inventory_id:
        raise HermesModelInventoryStorageError("private_inventory_malformed")
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes:
        raise HermesModelInventoryStorageError("private_inventory_malformed")


def _route_to_private_artifact(route: HermesModelRoute) -> dict[str, object]:
    data = asdict(route)
    data["aliases"] = list(route.aliases)
    data["capabilities"] = {tier: route.capabilities[tier] for tier in MODEL_TIERS}
    return data


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
