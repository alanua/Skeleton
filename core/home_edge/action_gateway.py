from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .actions import (
    ALLOWED_ACTIONS,
    HomeEdgeActionError,
    execute_home_edge_action,
    validate_action_request,
)


AUTH_KEY_ID_ENV = "SKELETON_HOME_EDGE_ACTION_KEY_ID"
AUTH_SECRET_ENV = "SKELETON_HOME_EDGE_ACTION_HMAC_SECRET"
AUTH_SECRET_FILE_ENV = "SKELETON_HOME_EDGE_ACTION_HMAC_SECRET_FILE"
DEFAULT_REQUEST_TTL_SECONDS = 120
DEFAULT_CACHE_TTL_SECONDS = 600


class HomeEdgeGatewayError(ValueError):
    """Public-safe gateway rejection."""


@dataclass
class MemoryReplayStore:
    ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    receipts: dict[str, tuple[float, str, dict[str, Any]]] = field(default_factory=dict)
    nonces: dict[str, float] = field(default_factory=dict)

    def get_receipt(self, key: str, body_hash: str, now: float) -> dict[str, Any] | None:
        self.prune(now)
        entry = self.receipts.get(key)
        if entry is None:
            return None
        _created, stored_hash, receipt = entry
        if stored_hash != body_hash:
            raise HomeEdgeGatewayError("idempotency key reused with different body")
        cached = json.loads(json.dumps(receipt))
        cached["idempotent_replay"] = True
        return cached

    def remember_receipt(
        self,
        key: str,
        body_hash: str,
        receipt: dict[str, Any],
        now: float,
    ) -> None:
        self.receipts[key] = (now, body_hash, json.loads(json.dumps(receipt)))

    def remember_nonce(self, nonce: str, now: float) -> None:
        self.prune(now)
        if nonce in self.nonces:
            raise HomeEdgeGatewayError("nonce replay rejected")
        self.nonces[nonce] = now

    def prune(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        self.receipts = {
            key: value for key, value in self.receipts.items() if value[0] >= cutoff
        }
        self.nonces = {
            nonce: created for nonce, created in self.nonces.items() if created >= cutoff
        }


@dataclass
class HomeEdgeActionGateway:
    hmac_secret: bytes
    key_id: str
    store: MemoryReplayStore = field(default_factory=MemoryReplayStore)
    request_ttl_seconds: int = DEFAULT_REQUEST_TTL_SECONDS
    profile: Any | None = None
    transport: Any | None = None
    diagnostic_transport: Any | None = None

    @classmethod
    def from_environment(cls, **kwargs: Any) -> "HomeEdgeActionGateway":
        key_id = os.environ.get(AUTH_KEY_ID_ENV, "").strip()
        secret = os.environ.get(AUTH_SECRET_ENV, "").strip()
        secret_file = os.environ.get(AUTH_SECRET_FILE_ENV, "").strip()
        if not secret and secret_file:
            secret = Path(secret_file).expanduser().read_text(encoding="utf-8").strip()
        if not key_id or not secret:
            raise HomeEdgeGatewayError("gateway authentication is not configured")
        return cls(hmac_secret=secret.encode("utf-8"), key_id=key_id, **kwargs)

    def public_unauthenticated_status(self) -> dict[str, str]:
        return {
            "schema": "skeleton.home_edge.action_gateway.status.v1",
            "status": "authentication_required",
        }

    def authenticated_capabilities(self) -> dict[str, Any]:
        return {
            "schema": "skeleton.home_edge.action_gateway.capabilities.v1",
            "node_id": "home-edge-01",
            "actions": sorted(ALLOWED_ACTIONS),
        }

    def handle_json(
        self,
        body: bytes,
        *,
        key_id: str | None,
        signature: str | None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        self._authenticate(body, key_id=key_id, signature=signature)
        decoded = _decode_body(body)
        validated = validate_action_request(decoded)
        current_dt = now or datetime.now(UTC)
        current = current_dt.timestamp()
        _validate_fresh_timestamp(validated["timestamp"], current_dt, self.request_ttl_seconds)
        body_hash = _body_hash(decoded)
        cached = self.store.get_receipt(validated["idempotency_key"], body_hash, current)
        if cached is not None:
            return cached
        self.store.remember_nonce(validated["nonce"], current)
        receipt = execute_home_edge_action(
            validated,
            profile=self.profile,
            transport=self.transport,
            diagnostic_transport=self.diagnostic_transport,
            now=current_dt,
        )
        receipt["idempotent_replay"] = False
        self.store.remember_receipt(validated["idempotency_key"], body_hash, receipt, current)
        return receipt

    def _authenticate(
        self,
        body: bytes,
        *,
        key_id: str | None,
        signature: str | None,
    ) -> None:
        if key_id != self.key_id:
            raise HomeEdgeGatewayError("authentication failed")
        if not isinstance(signature, str) or not signature.startswith("sha256="):
            raise HomeEdgeGatewayError("authentication failed")
        expected = "sha256=" + hmac.new(
            self.hmac_secret,
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HomeEdgeGatewayError("authentication failed")


def sign_body(body: bytes, secret: bytes) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def _decode_body(body: bytes) -> dict[str, Any]:
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HomeEdgeGatewayError("request body must be JSON") from exc
    if not isinstance(decoded, dict):
        raise HomeEdgeGatewayError("request body must be an object")
    return decoded


def _body_hash(body: dict[str, Any]) -> str:
    rendered = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _validate_fresh_timestamp(value: str, now: datetime, ttl_seconds: int) -> None:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    age = abs((now.astimezone(UTC) - parsed).total_seconds())
    if age > ttl_seconds:
        raise HomeEdgeGatewayError("stale timestamp rejected")
