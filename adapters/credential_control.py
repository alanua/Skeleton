from __future__ import annotations

from collections.abc import Mapping
import re

from core.credential_broker import (
    CredentialBroker,
    CredentialBrokerError,
    CredentialRequestError,
)


CONTROL_SCHEMA = "skeleton.credential_control.v1"
_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{1,127}$")


class CredentialControlAdapter:
    """Bounded per-service control surface: receipts only, never secret material."""

    def __init__(self, broker: CredentialBroker, *, service_id: str) -> None:
        if not isinstance(broker, CredentialBroker):
            raise TypeError("typed_credential_broker_required")
        if not isinstance(service_id, str) or not _SERVICE_ID_RE.fullmatch(service_id.strip()):
            raise ValueError("invalid_control_service_id")
        self._broker = broker
        self._service_id = service_id.strip()

    @property
    def service_id(self) -> str:
        return self._service_id

    def invoke(self, operation: str, payload: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(operation, str) or not isinstance(payload, Mapping):
            return self._blocked("INVALID_REQUEST")
        allowed = {"alias", "action_id"}
        if set(payload) - allowed:
            return self._blocked("UNKNOWN_FIELDS")
        alias = payload.get("alias")
        if not isinstance(alias, str):
            return self._blocked("MISSING_CREDENTIAL_ALIAS")
        try:
            if operation in {"credential_probe", "credential_find"}:
                receipt = self._broker.probe(service_id=self._service_id, alias=alias)
            elif operation == "credential_use":
                action_id = payload.get("action_id")
                if not isinstance(action_id, str):
                    return self._blocked("MISSING_ACTION_ID")
                receipt = self._broker.use(
                    service_id=self._service_id,
                    alias=alias,
                    action_id=action_id,
                )
            else:
                return self._blocked("UNSUPPORTED_OPERATION")
        except (CredentialRequestError, CredentialBrokerError):
            return self._blocked("REQUEST_REJECTED")
        return {
            "schema": CONTROL_SCHEMA,
            "service_id": self._service_id,
            "result": receipt.to_public_mapping(),
        }

    def _blocked(self, reason_class: str) -> dict[str, object]:
        return {
            "schema": CONTROL_SCHEMA,
            "service_id": self._service_id,
            "result": {
                "status": "BLOCKED",
                "reason_class": reason_class,
            },
        }
