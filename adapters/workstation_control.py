"""Synthetic, non-host CAD-like workspace; not a vendor or live CAD adapter."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Mapping

from core.workstation_agent import WorkstationRejected


class SyntheticWorkstationAdapter:
    """Fully deterministic in-memory workstation test adapter.

    No filesystem/network/native application imports. Access to entities is private to
    this process; public receipts reveal only revisions and counts.
    """

    def __init__(self) -> None:
        self._objects: dict[str, dict[str, object]] = {}
        self._revision = 0
        self._checkpoints: dict[str, tuple[int, dict[str, dict[str, object]]]] = {}

    def public_snapshot(self) -> dict[str, object]:
        return {"revision": self._revision, "object_count": len(self._objects)}

    def _check_properties(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict) or len(value) > 20:
            raise WorkstationRejected("INVALID_PROPERTIES")
        try:
            data = json.dumps(value, allow_nan=False, sort_keys=True)
            if len(data.encode("utf-8")) > 2048:
                raise WorkstationRejected("INVALID_PROPERTIES")
        except (TypeError, ValueError) as exc:
            raise WorkstationRejected("INVALID_PROPERTIES") from exc
        if any(not isinstance(k, str) or not k.isidentifier() for k in value):
            raise WorkstationRejected("INVALID_PROPERTIES")
        return deepcopy(value)

    def _fields(self, operation: str, payload: Mapping[str, object]) -> dict[str, object]:
        allowed = {
            "INSPECT": {"entity_id"}, "SNAPSHOT": set(),
            "PROPOSE": {"entity_id", "properties"}, "APPLY": {"entity_id", "properties"},
            "CHECKPOINT": {"checkpoint_id"}, "ROLLBACK": {"checkpoint_id"},
        }[operation]
        if set(payload) != allowed:
            raise WorkstationRejected("INVALID_OPERATION_FIELDS")
        data = dict(payload)
        for key in ("entity_id", "checkpoint_id"):
            if key in data and (not isinstance(data[key], str) or not data[key]
                                or len(data[key]) > 80 or not data[key].replace("_", "").replace("-", "").isalnum()):
                raise WorkstationRejected("INVALID_ENTITY_OR_CHECKPOINT_ID")
        if "properties" in data:
            data["properties"] = self._check_properties(data["properties"])
        return data

    def handle(self, operation: str, payload: Mapping[str, object]) -> dict[str, object]:
        if operation not in {"INSPECT", "SNAPSHOT", "PROPOSE", "APPLY", "CHECKPOINT", "ROLLBACK"}:
            return {"status": "REJECTED", "reason": "OPERATION_NOT_SUPPORTED"}
        try:
            data = self._fields(operation, payload)
        except WorkstationRejected:
            return {"status": "REJECTED", "reason": "ADAPTER_PAYLOAD_REJECTED"}
        if operation in ("INSPECT", "SNAPSHOT", "PROPOSE"):
            return {"status": "OK"}  # No geometry or client content in public outputs.
        if operation == "CHECKPOINT":
            name = str(data["checkpoint_id"])
            if name in self._checkpoints:
                return {"status": "REJECTED", "reason": "CHECKPOINT_EXISTS"}
            self._checkpoints[name] = (self._revision, deepcopy(self._objects))
            return {"status": "OK"}
        if operation == "ROLLBACK":
            name = str(data["checkpoint_id"])
            if name not in self._checkpoints:
                return {"status": "REJECTED", "reason": "CHECKPOINT_UNKNOWN"}
            self._revision, self._objects = deepcopy(self._checkpoints[name])
            return {"status": "OK"}
        if operation == "APPLY":
            entity = str(data["entity_id"])
            fields = data["properties"]
            before = deepcopy(self._objects.get(entity, {}))
            before.update(fields)
            self._objects[entity] = before
            self._revision += 1
            return {"status": "OK"}
        return {"status": "REJECTED", "reason": "OPERATION_NOT_SUPPORTED"}