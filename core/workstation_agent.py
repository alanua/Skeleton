"""Bounded, offline-first workstation command plane: no transport or host execution.

This file is an implementation *contract*, not a deployed machine agent.
Trusted node attestation, server approval authority and networking are injected by
private, separately approved deployment adapters. Fail closed if absent.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import StrEnum
import hashlib
import json
import math
import re
import time
from typing import Callable, Mapping, Protocol


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_OPERATIONS = frozenset({"INSPECT", "SNAPSHOT", "PROPOSE", "APPLY", "CHECKPOINT", "ROLLBACK"})
_MUTATIONS = frozenset({"APPLY", "CHECKPOINT", "ROLLBACK"})
_MAX_TTL = 900.0
_MAX_PAYLOAD = 16384


class WorkstationRejected(ValueError):
    """Fail-closed typed validation failure; never embed private request values."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise WorkstationRejected("INVALID_" + field_name.upper())
    return value


def _time(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise WorkstationRejected("INVALID_" + field_name.upper())
    return float(value)


def _json(value: object) -> str:
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(text.encode("utf-8")) > _MAX_PAYLOAD:
            raise WorkstationRejected("PAYLOAD_TOO_LARGE")
        return text
    except (TypeError, ValueError) as exc:
        raise WorkstationRejected("INVALID_PAYLOAD") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class Trust(StrEnum):
    TRUSTED = "TRUSTED"
    PENDING = "PENDING"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class LeaseMode(StrEnum):
    OBSERVE = "OBSERVE"
    APPLY_APPROVED = "APPLY_APPROVED"


@dataclass(frozen=True, slots=True)
class NodeIdentity:
    node_id: str
    installation_id: str
    trust: Trust

    def __post_init__(self) -> None:
        _id(self.node_id, "node_id")
        _id(self.installation_id, "installation_id")
        object.__setattr__(self, "trust", Trust(self.trust))


@dataclass(frozen=True, slots=True)
class NodeSession:
    node_session_id: str
    node_id: str
    authenticated_at: float
    expires_at: float
    connected: bool = True

    def __post_init__(self) -> None:
        _id(self.node_session_id, "node_session_id")
        _id(self.node_id, "node_id")
        _time(self.authenticated_at, "authenticated_at")
        _time(self.expires_at, "expires_at")
        if self.expires_at <= self.authenticated_at or not isinstance(self.connected, bool):
            raise WorkstationRejected("INVALID_NODE_SESSION")


@dataclass(frozen=True, slots=True)
class ApplicationSession:
    application_session_id: str
    node_session_id: str
    workspace_id: str
    provider_capability: str
    context_revision: int = 0

    def __post_init__(self) -> None:
        for label in ("application_session_id", "node_session_id", "workspace_id", "provider_capability"):
            _id(getattr(self, label), label)
        if type(self.context_revision) is not int or self.context_revision < 0:
            raise WorkstationRejected("INVALID_CONTEXT_REVISION")


@dataclass(frozen=True, slots=True)
class InteractionLease:
    lease_id: str
    application_session_id: str
    node_session_id: str
    mode: LeaseMode
    expires_at: float
    revoked: bool = False

    def __post_init__(self) -> None:
        for label in ("lease_id", "application_session_id", "node_session_id"):
            _id(getattr(self, label), label)
        object.__setattr__(self, "mode", LeaseMode(self.mode))
        _time(self.expires_at, "expires_at")
        if not isinstance(self.revoked, bool):
            raise WorkstationRejected("INVALID_LEASE_REVOCATION")


@dataclass(frozen=True, slots=True)
class CommandEnvelope:
    command_id: str
    idempotency_key: str
    node_id: str
    node_session_id: str
    application_session_id: str
    lease_id: str
    workspace_id: str
    operation: str
    issued_at: float
    expires_at: float
    expected_revision: int
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for label in ("command_id", "idempotency_key", "node_id", "node_session_id",
                      "application_session_id", "lease_id", "workspace_id"):
            _id(getattr(self, label), label)
        if self.operation not in _OPERATIONS:
            raise WorkstationRejected("UNSUPPORTED_OPERATION")
        _time(self.issued_at, "issued_at")
        _time(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at or self.expires_at - self.issued_at > _MAX_TTL:
            raise WorkstationRejected("INVALID_COMMAND_TTL")
        if type(self.expected_revision) is not int or self.expected_revision < 0:
            raise WorkstationRejected("INVALID_EXPECTED_REVISION")
        if not isinstance(self.payload, Mapping):
            raise WorkstationRejected("INVALID_PAYLOAD")
        # A defensive copy prevents post-approval mutation by callers.
        safe = deepcopy(dict(self.payload))
        _json(safe)
        object.__setattr__(self, "payload", safe)

    def fingerprint(self) -> str:
        return _digest({"command_id": self.command_id, "idempotency_key": self.idempotency_key,
                        "node_id": self.node_id, "node_session_id": self.node_session_id,
                        "application_session_id": self.application_session_id, "lease_id": self.lease_id,
                        "workspace_id": self.workspace_id, "operation": self.operation,
                        "issued_at": self.issued_at, "expires_at": self.expires_at,
                        "expected_revision": self.expected_revision, "payload": self.payload})


@dataclass(frozen=True, slots=True)
class GateAuthorization:
    """Only supplied by a *registered trusted authority* in real deployment."""
    approved: bool
    node_id: str
    node_session_id: str
    lease_id: str
    command_id: str
    operation: str
    expires_at: float
    approval_reference: str

    def __post_init__(self) -> None:
        for label in ("node_id", "node_session_id", "lease_id", "command_id", "approval_reference"):
            _id(getattr(self, label), label)
        if self.operation not in _OPERATIONS or not isinstance(self.approved, bool):
            raise WorkstationRejected("INVALID_GATE_AUTHORIZATION")
        _time(self.expires_at, "expires_at")


class TrustedGate(Protocol):
    def authorize(self, command: CommandEnvelope) -> GateAuthorization:
        ...


class BoundedAdapter(Protocol):
    def public_snapshot(self) -> dict[str, object]:
        ...

    def handle(self, operation: str, payload: Mapping[str, object]) -> dict[str, object]:
        ...


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    status: str
    reason_class: str
    operation: str
    before_revision: int
    after_revision: int
    receipt_hash: str
    acknowledged: bool = False

    def public(self) -> dict[str, object]:
        return {"schema": "skeleton.workstation_receipt.v1", "status": self.status,
                "reason_class": self.reason_class, "operation": self.operation,
                "before_revision": self.before_revision, "after_revision": self.after_revision,
                "receipt_hash": self.receipt_hash, "acknowledged": self.acknowledged}


class CommandJournal(Protocol):
    def unresolved_count(self) -> int: ...
    def count(self) -> int: ...
    def reserve(self, command: CommandEnvelope) -> str: ...
    def finalize(self, command: CommandEnvelope, receipt: CommandReceipt) -> None: ...
    def list_pending(self, limit: int = 100) -> tuple[dict[str, object], ...]: ...
    def acknowledge(self, receipt_hash: str, signed_ack: bytes) -> bool: ...


class WorkstationAgent:
    """Pure offline-safe command state-machine; not a network endpoint.

    No executable path, shell, credentials, filesystem, or network hooks exist.
    Untrusted callers cannot specify an executable, adapter, or Gate on each call.
    """

    def __init__(self, *, identity: NodeIdentity, node_session: NodeSession,
                 application: ApplicationSession, lease: InteractionLease,
                 gate: TrustedGate | None, adapter: BoundedAdapter,
                 clock: Callable[[], float] = time.time,
                 journal: CommandJournal | None = None) -> None:
        self.identity = identity
        self.node_session = node_session
        self.application = application
        self.lease = lease
        self._gate = gate
        self._adapter = adapter
        self._clock = clock
        self._journal = journal
        self._seen: dict[str, tuple[str, CommandReceipt]] = {}
        self._outbox: list[CommandReceipt] = []
        self._paused = False
        self._aborted = False
        self._connected = bool(node_session.connected)
        if journal is not None:
            try:
                if journal.unresolved_count():
                    self._aborted = True
                    self._paused = True
            except Exception:
                self._aborted = True
                self._paused = True
        if (node_session.node_id != identity.node_id
                or application.node_session_id != node_session.node_session_id
                or lease.application_session_id != application.application_session_id
                or lease.node_session_id != node_session.node_session_id):
            raise WorkstationRejected("SESSION_BINDING_MISMATCH")

    def set_transport_online(self, connected: bool) -> None:
        if not isinstance(connected, bool):
            raise WorkstationRejected("INVALID_TRANSPORT_STATE")
        self._connected = connected

    def local_pause(self) -> None:
        self._paused = True

    def local_abort(self) -> None:
        self._aborted = True
        self._paused = True

    def public_status(self) -> dict[str, object]:
        return {"schema": "skeleton.workstation_status.v1", "online": self._connected,
                "trusted": self.identity.trust is Trust.TRUSTED, "paused": self._paused,
                "aborted": self._aborted,
                "pending_receipts": self._journal.count() if self._journal is not None else len(self._outbox),
                "processed_commands": len(self._seen)}

    def pending_receipts(self) -> tuple[dict[str, object], ...]:
        # No command payload, workspace, provider, private device name or secret.
        if self._journal is not None:
            return self._journal.list_pending()
        return tuple(receipt.public() for receipt in self._outbox)

    def ack_receipt(self, receipt_hash: str, *, signed_ack: bytes = b"") -> bool:
        _id(receipt_hash, "receipt_hash")
        if self._journal is not None:
            return self._journal.acknowledge(receipt_hash, signed_ack)
        before = len(self._outbox)
        self._outbox = [r for r in self._outbox if r.receipt_hash != receipt_hash]
        return len(self._outbox) < before

    def _reject(self, command: CommandEnvelope, reason: str) -> CommandReceipt:
        version = self._adapter.public_snapshot().get("revision", 0)
        return self._receipt(command, "BLOCKED", reason, version, version)

    def _receipt(self, command: CommandEnvelope, status: str, reason: str,
                 before: int, after: int) -> CommandReceipt:
        receipt_hash = _digest({"command_fingerprint": command.fingerprint(),
                                "status": status, "reason_class": reason,
                                "before_revision": before, "after_revision": after})
        return CommandReceipt(status, reason, command.operation, before, after, receipt_hash)

    def submit(self, command: CommandEnvelope) -> CommandReceipt:
        if not isinstance(command, CommandEnvelope):
            raise WorkstationRejected("TYPED_COMMAND_REQUIRED")
        now = _time(self._clock(), "clock")
        if now > command.expires_at or command.issued_at > now + 30:
            return self._reject(command, "COMMAND_EXPIRED_OR_FUTURE")
        fp = command.fingerprint()
        previous = self._seen.get(command.idempotency_key)
        if previous is not None:
            if previous[0] != fp:
                return self._reject(command, "IDEMPOTENCY_CONFLICT")
            return previous[1]  # replay gives identical receipt, never a second mutation
        reason = self._preflight(command, now)
        if reason:
            return self._reject(command, reason)
        gate = self._gate
        if gate is None:
            return self._reject(command, "TRUSTED_GATE_MISSING")
        try:
            grant = gate.authorize(command)
        except Exception:
            return self._reject(command, "GATE_UNAVAILABLE")
        if not isinstance(grant, GateAuthorization) or not grant.approved:
            return self._reject(command, "GATE_DENIED")
        if (grant.node_id != command.node_id or grant.node_session_id != command.node_session_id
                or grant.lease_id != command.lease_id or grant.command_id != command.command_id
                or grant.operation != command.operation or grant.expires_at <= now):
            return self._reject(command, "GATE_BINDING_MISMATCH")
        journal = self._journal
        if journal is not None:
            try:
                reservation = journal.reserve(command)
            except Exception:
                self.local_abort()
                return self._reject(command, "JOURNAL_UNAVAILABLE")
            if reservation != "NEW":
                return self._reject(command, {
                    "PENDING": "JOURNAL_UNRESOLVED",
                    "COMPLETED": "ALREADY_COMPLETED",
                    "CONFLICT": "IDEMPOTENCY_CONFLICT",
                }.get(reservation, "JOURNAL_UNAVAILABLE"))
        before = self._adapter.public_snapshot()["revision"]
        try:
            result = self._adapter.handle(command.operation, command.payload)
            after = self._adapter.public_snapshot()["revision"]
            if not isinstance(result, dict) or result.get("status") not in {"OK", "REJECTED"}:
                if journal is not None:
                    self.local_abort()
                return self._reject(command, "ADAPTER_CONTRACT_VIOLATION")
            status = "APPLIED" if result["status"] == "OK" else "BLOCKED"
            reason = "OK" if status == "APPLIED" else str(result.get("reason", "ADAPTER_REJECTED"))
            if not re.fullmatch(r"[A-Z_]{2,64}", reason):
                reason = "ADAPTER_REJECTED"
        except Exception:
            # A journal PENDING record survives a crash/failure; do not auto-retry.
            if journal is not None:
                self.local_abort()
            return self._reject(command, "ADAPTER_FAILED")
        receipt = self._receipt(command, status, reason, before, after)
        if journal is not None:
            try:
                journal.finalize(command, receipt)
            except Exception:
                self.local_abort()
                return self._reject(command, "JOURNAL_COMMIT_FAILED")
        if status == "APPLIED" and command.operation in _MUTATIONS:
            self.application = replace(self.application, context_revision=after)
        self._seen[command.idempotency_key] = (fp, receipt)
        if journal is None:
            self._outbox.append(receipt)
        return receipt

    def _preflight(self, command: CommandEnvelope, now: float) -> str | None:
        if self._aborted or self._paused:
            return "LOCAL_PAUSE_OR_ABORT"
        if self.identity.trust is not Trust.TRUSTED:
            return "DEVICE_NOT_TRUSTED"
        if not self._connected or not self.node_session.connected:
            return "NODE_OFFLINE"
        if now >= self.node_session.expires_at or now < self.node_session.authenticated_at:
            return "NODE_SESSION_EXPIRED"
        if self.lease.revoked or now >= self.lease.expires_at:
            return "LEASE_EXPIRED_OR_REVOKED"
        if (command.node_id != self.identity.node_id
                or command.node_session_id != self.node_session.node_session_id
                or command.application_session_id != self.application.application_session_id
                or command.lease_id != self.lease.lease_id
                or command.workspace_id != self.application.workspace_id):
            return "COMMAND_BOUNDARY_MISMATCH"
        if (command.expected_revision != self.application.context_revision
                or command.expected_revision != self._adapter.public_snapshot()["revision"]):
            return "STALE_APPLICATION_CONTEXT"
        if command.operation in _MUTATIONS and self.lease.mode is not LeaseMode.APPLY_APPROVED:
            return "OBSERVE_LEASE_FORBIDS_MUTATION"
        return None
