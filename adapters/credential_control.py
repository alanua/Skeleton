from __future__ import annotations

from collections.abc import Mapping

from core.credential_broker import (
    CredentialBroker,
    CredentialBrokerError,
    CredentialRequestError,
)


CONTROL_SCHEMA = "skeleton.credential_control.v1"


class CredentialControlAdapter:
    """Bounded control surface: status/use receipts only, never secret material."""

    def __init__(self, broker: CredentialBroker) -> None:
        if not isinstance(broker, CredentialBroker):
            raise TypeError("typed_credential_broker_required")
        self._broker = broker

    def invoke(self, operation: str, payload: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(operation, str) or not isinstance(payload, Mapping):
            return self._blocked("INVALID_REQUEST")
        allowed = {"service_id", "alias", "action_id"}
        if set(payload) - allowed:
            return self._blocked("UNKNOWN_FIELDS")
        service_id = payload.get("service_id")
        alias = payload.get("alias")
        if not isinstance(service_id, str) or not isinstance(alias, str):
            return self._blocked("MISSING_BINDING_IDENTITY")
        try:
            if operation in {"credential_probe", "credential_find"}:
                receipt = self._broker.probe(service_id=service_id, alias=alias)
            elif operation == "credential_use":
                action_id = payload.get("action_id")
                if not isinstance(action_id, str):
                    return self._blocked("MISSING_ACTION_ID")
                receipt = self._broker.use(
                    service_id=service_id,
                    alias=alias,
                    action_id=action_id,
                )
            else:
                return self._blocked("UNSUPPORTED_OPERATION")
        except (CredentialRequestError, CredentialBrokerError):
            return self._blocked("REQUEST_REJECTED")
        return {
            "schema": CONTROL_SCHEMA,
            "result": receipt.to_public_mapping(),
        }

    @staticmethod
    def _blocked(reason_class: str) -> dict[str, object]:
        return {
            "schema": CONTROL_SCHEMA,
            "result": {
                "status": "BLOCKED",
                "reason_class": reason_class,
            },
        }
