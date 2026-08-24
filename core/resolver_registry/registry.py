from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .models import (
    CANON_SCHEMA,
    STATE_SCHEMA,
    CanonicalCapabilityRecord,
    CapabilityStateRecord,
    ResolverCapabilityManifest,
    ResolverCapabilityError,
)


class ResolverCapabilityRegistry:
    """File-backed canon/state adapter for resolver capability exchange."""

    def __init__(self, *, canon_path: Path, state_path: Path) -> None:
        self.canon_path = Path(canon_path)
        self.state_path = Path(state_path)

    def register_canonical(
        self,
        manifest: ResolverCapabilityManifest,
        *,
        package_hash: str,
        manifest_hash: str,
    ) -> CanonicalCapabilityRecord:
        record = CanonicalCapabilityRecord(
            capability_id=manifest.capability_id,
            version=manifest.version,
            supported_hosts=manifest.supported_hosts,
            package_hash=package_hash,
            manifest_hash=manifest_hash,
            status="approved" if manifest.approvals.get("merge") else "implemented",
            operations={
                "deploy": str(manifest.deploy["operation_id"]),
                "verify": str(manifest.verify["operation_id"]),
                "rollback": str(manifest.rollback["operation_id"]),
            },
        )
        data = self._load_json(self.canon_path, default={"schema": CANON_SCHEMA, "capabilities": {}})
        key = f"{record.capability_id}@{record.version}"
        data.setdefault("capabilities", {})[key] = record.to_mapping()
        self._atomic_json(self.canon_path, data)
        return record

    def canonical(self, capability_id: str, version: str) -> dict[str, Any] | None:
        data = self._load_json(self.canon_path, default={"schema": CANON_SCHEMA, "capabilities": {}})
        value = data.get("capabilities", {}).get(f"{capability_id}@{version}")
        return dict(value) if isinstance(value, Mapping) else None

    def state(self, capability_id: str, node_id: str) -> CapabilityStateRecord | None:
        data = self._load_json(self.state_path, default={"schema": STATE_SCHEMA, "installations": {}})
        value = data.get("installations", {}).get(f"{node_id}:{capability_id}")
        if not isinstance(value, Mapping):
            return None
        return CapabilityStateRecord(
            capability_id=str(value["capability_id"]),
            node_id=str(value["node_id"]),
            status=str(value["status"]),
            active_version=value.get("active_version"),
            package_hash=value.get("package_hash"),
            rollback_version=value.get("rollback_version"),
            last_success=value.get("last_success"),
            last_failure=value.get("last_failure"),
            deployment_receipts=tuple(value.get("deployment_receipts") or ()),
        )

    def record_state(self, record: CapabilityStateRecord) -> CapabilityStateRecord:
        data = self._load_json(self.state_path, default={"schema": STATE_SCHEMA, "installations": {}})
        data.setdefault("installations", {})[f"{record.node_id}:{record.capability_id}"] = record.to_mapping()
        self._atomic_json(self.state_path, data)
        return record

    def append_receipt(
        self,
        *,
        capability_id: str,
        node_id: str,
        status: str,
        receipt: Mapping[str, Any],
        active_version: str | None = None,
        package_hash: str | None = None,
        rollback_version: str | None = None,
    ) -> CapabilityStateRecord:
        current = self.state(capability_id, node_id) or CapabilityStateRecord(
            capability_id=capability_id,
            node_id=node_id,
            status="discovered",
        )
        stamped = {"recorded_at": _now(), **dict(receipt)}
        next_record = replace(
            current,
            status=status,
            active_version=active_version if active_version is not None else current.active_version,
            package_hash=package_hash if package_hash is not None else current.package_hash,
            rollback_version=rollback_version if rollback_version is not None else current.rollback_version,
            last_success=_now() if status in {"canary", "active", "rolled_back"} else current.last_success,
            last_failure=_now() if status in {"degraded", "disabled"} else current.last_failure,
            deployment_receipts=current.deployment_receipts + (stamped,),
        )
        return self.record_state(next_record)

    def discover_compatible(self, *, host: str, runtime_version: str, package_hash: str | None = None) -> list[dict[str, Any]]:
        data = self._load_json(self.canon_path, default={"schema": CANON_SCHEMA, "capabilities": {}})
        matches: list[dict[str, Any]] = []
        for record in data.get("capabilities", {}).values():
            if not isinstance(record, Mapping):
                continue
            if host not in record.get("supported_hosts", ()):
                continue
            if package_hash is not None and record.get("package_hash") != package_hash:
                continue
            match = dict(record)
            match["runtime_version_checked"] = runtime_version
            matches.append(match)
        return matches

    @staticmethod
    def _load_json(path: Path, *, default: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return dict(default)
        if not isinstance(value, dict):
            raise ResolverCapabilityError(f"{path} must contain a JSON object")
        return value

    @staticmethod
    def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
