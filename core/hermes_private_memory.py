from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from core.private_memory import (
    PRIVATE_MEMORY_HEALTHCHECK_SCHEMA,
    healthcheck_private_memory,
    record_task_state_heartbeat,
    write_public_heartbeat,
)


HERMES_PRIVATE_MEMORY_REPORT_SCHEMA = "skeleton.hermes_private_memory.report.v0"

_SAFE_HERMES_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_HERMES_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.: -]{0,127}$")
_ALLOWED_OPERATIONS = frozenset({"orient", "heartbeat", "note"})
_WRITE_DISABLED_ACTION = "operator_enable_hermes_private_memory_write"
_UNSAFE_VALUE_MARKERS = (
    "/",
    "\\",
    "://",
    "secret",
    "token",
    "password",
    "credential",
    "sqlite",
    "select ",
    "insert ",
    "update ",
    "delete ",
    "create table",
)


@dataclass(frozen=True)
class HermesPrivateMemoryReport:
    schema: str
    status: str
    operation: str
    connector_schema: str
    connector_status: str
    db_configured: bool
    db_openable: bool
    integrity_ok: bool
    schema_present: bool
    writable_when_requested: bool
    heartbeat_ok: bool
    bridge_write_enabled: bool
    error_class: str | None
    next_operator_action: str


def orient_hermes_private_memory(
    *,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return a read-only, public-safe Hermes orientation report."""
    connector_report = healthcheck_private_memory(
        config_path=config_path,
        request_write=False,
        env=env,
    )
    return _bridge_report(
        operation="orient",
        connector_report=connector_report,
        bridge_write_enabled=False,
    )


def write_hermes_private_memory_heartbeat(
    heartbeat_id: str,
    *,
    write_enabled: bool = False,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Write one synthetic Hermes heartbeat only when explicitly enabled."""
    try:
        if not write_enabled:
            connector_report = healthcheck_private_memory(config_path=config_path, env=env)
            return _bridge_report(
                operation="heartbeat",
                connector_report=connector_report,
                bridge_write_enabled=False,
                status="BLOCKED",
                error_class="HermesPrivateMemoryWriteGateError",
                next_operator_action=_WRITE_DISABLED_ACTION,
            )

        heartbeat_id = _safe_hermes_id(heartbeat_id, "heartbeat_id")
        connector_report = write_public_heartbeat(
            heartbeat_id,
            source="hermes-private-memory-v0",
            config_path=config_path,
            env=env,
        )
        return _bridge_report(
            operation="heartbeat",
            connector_report=connector_report,
            bridge_write_enabled=True,
        )
    except Exception as exc:  # noqa: BLE001 - bridge reports must fail closed.
        return _blocked_bridge_report(
            operation="heartbeat",
            bridge_write_enabled=write_enabled,
            error_class=type(exc).__name__,
        )


def record_hermes_private_memory_note(
    note_id: str,
    state: str,
    *,
    write_enabled: bool = False,
    config_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Record one synthetic note marker through the connector task-state heartbeat."""
    try:
        if not write_enabled:
            connector_report = healthcheck_private_memory(config_path=config_path, env=env)
            return _bridge_report(
                operation="note",
                connector_report=connector_report,
                bridge_write_enabled=False,
                status="BLOCKED",
                error_class="HermesPrivateMemoryWriteGateError",
                next_operator_action=_WRITE_DISABLED_ACTION,
            )

        note_id = _safe_hermes_id(note_id, "note_id")
        state = _safe_hermes_text(state, "state")
        connector_report = record_task_state_heartbeat(
            note_id,
            state,
            source="hermes-private-memory-v0",
            config_path=config_path,
            env=env,
        )
        return _bridge_report(
            operation="note",
            connector_report=connector_report,
            bridge_write_enabled=True,
        )
    except Exception as exc:  # noqa: BLE001 - bridge reports must fail closed.
        return _blocked_bridge_report(
            operation="note",
            bridge_write_enabled=write_enabled,
            error_class=type(exc).__name__,
        )


class HermesPrivateMemoryError(Exception):
    """Base exception for Hermes private-memory bridge validation failures."""


class HermesPrivateMemoryPrivacyError(HermesPrivateMemoryError):
    """Raised when a bridge report would expose private details."""


class HermesPrivateMemoryWriteGateError(HermesPrivateMemoryError):
    """Raised when Hermes attempts a write without an explicit gate."""


def _bridge_report(
    *,
    operation: str,
    connector_report: Mapping[str, object],
    bridge_write_enabled: bool,
    status: str | None = None,
    error_class: str | None = None,
    next_operator_action: str | None = None,
) -> dict[str, object]:
    operation = _safe_operation(operation)
    connector_status = _safe_status(connector_report.get("status"))
    connector_error_class = _optional_safe_token(connector_report.get("error_class"), "error_class")
    connector_action = _safe_action(connector_report.get("next_operator_action"))
    report = HermesPrivateMemoryReport(
        schema=HERMES_PRIVATE_MEMORY_REPORT_SCHEMA,
        status=_safe_status(status or connector_status),
        operation=operation,
        connector_schema=_safe_connector_schema(connector_report.get("schema")),
        connector_status=connector_status,
        db_configured=_bool(connector_report.get("db_configured")),
        db_openable=_bool(connector_report.get("db_openable")),
        integrity_ok=_bool(connector_report.get("integrity_ok")),
        schema_present=_bool(connector_report.get("schema_present")),
        writable_when_requested=_bool(connector_report.get("writable_when_requested")),
        heartbeat_ok=_bool(connector_report.get("heartbeat_ok")),
        bridge_write_enabled=bridge_write_enabled,
        error_class=error_class or connector_error_class,
        next_operator_action=next_operator_action or connector_action,
    )
    return _sanitize_report(report)


def _sanitize_report(report: HermesPrivateMemoryReport) -> dict[str, object]:
    data = asdict(report)
    for key, value in data.items():
        if key.lower() in {"path", "payload", "content", "secret", "token", "sql"}:
            raise HermesPrivateMemoryPrivacyError("unsafe report key")
        if isinstance(value, str) and _looks_private(value):
            raise HermesPrivateMemoryPrivacyError("unsafe report value")
    return data


def _blocked_bridge_report(
    *,
    operation: str,
    bridge_write_enabled: bool,
    error_class: str,
) -> dict[str, object]:
    report = HermesPrivateMemoryReport(
        schema=HERMES_PRIVATE_MEMORY_REPORT_SCHEMA,
        status="BLOCKED",
        operation=_safe_operation(operation),
        connector_schema=PRIVATE_MEMORY_HEALTHCHECK_SCHEMA,
        connector_status="BLOCKED",
        db_configured=False,
        db_openable=False,
        integrity_ok=False,
        schema_present=False,
        writable_when_requested=False,
        heartbeat_ok=False,
        bridge_write_enabled=bridge_write_enabled,
        error_class=_optional_safe_token(error_class, "error_class"),
        next_operator_action="configure_private_memory",
    )
    return _sanitize_report(report)


def _safe_hermes_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_HERMES_ID_RE.fullmatch(value):
        raise ValueError(f"{name} must be a synthetic public-safe id")
    return value


def _safe_hermes_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_HERMES_TEXT_RE.fullmatch(value):
        raise ValueError(f"{name} must be synthetic public-safe text")
    if _looks_private(value):
        raise ValueError(f"{name} must be synthetic public-safe text")
    return value


def _safe_operation(value: str) -> str:
    if value not in _ALLOWED_OPERATIONS:
        raise HermesPrivateMemoryPrivacyError("unsafe operation")
    return value


def _safe_connector_schema(value: object) -> str:
    if value != PRIVATE_MEMORY_HEALTHCHECK_SCHEMA:
        raise HermesPrivateMemoryPrivacyError("unsafe connector schema")
    return value


def _safe_status(value: object) -> str:
    if value not in {"DONE", "BLOCKED"}:
        raise HermesPrivateMemoryPrivacyError("unsafe status")
    return str(value)


def _safe_action(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_HERMES_TEXT_RE.fullmatch(value):
        raise HermesPrivateMemoryPrivacyError("unsafe next action")
    if _looks_private(value):
        raise HermesPrivateMemoryPrivacyError("unsafe next action")
    return value


def _optional_safe_token(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SAFE_HERMES_ID_RE.fullmatch(value):
        raise HermesPrivateMemoryPrivacyError(f"unsafe {name}")
    if _looks_private(value):
        raise HermesPrivateMemoryPrivacyError(f"unsafe {name}")
    return value


def _bool(value: object) -> bool:
    return value is True


def _looks_private(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _UNSAFE_VALUE_MARKERS)
