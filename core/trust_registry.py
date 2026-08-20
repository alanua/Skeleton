from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable, Mapping

from core.machine_identity import MachineIdentity


DEFAULT_MAX_VERIFICATION_AGE = timedelta(days=30)


class InvalidTrustBinding(ValueError):
    pass


class TrustState(str, Enum):
    PENDING = "PENDING"
    TRUSTED = "TRUSTED"
    REVOKED = "REVOKED"
    ROTATING = "ROTATING"


@dataclass(frozen=True)
class TrustBinding:
    identity: MachineIdentity
    trust_state: TrustState
    allowed_transports: frozenset[str]
    allowed_capabilities: frozenset[str]
    state_changed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.identity, MachineIdentity):
            raise InvalidTrustBinding("invalid_identity")
        try:
            state = self.trust_state if isinstance(self.trust_state, TrustState) else TrustState(self.trust_state)
        except (TypeError, ValueError) as exc:
            raise InvalidTrustBinding("invalid_trust_state") from exc
        object.__setattr__(self, "trust_state", state)
        if not isinstance(self.allowed_transports, frozenset) or not self.allowed_transports:
            raise InvalidTrustBinding("invalid_allowed_transports")
        if not isinstance(self.allowed_capabilities, frozenset) or not self.allowed_capabilities:
            raise InvalidTrustBinding("invalid_allowed_capabilities")
        if not self.allowed_transports <= self.identity.transport_profiles:
            raise InvalidTrustBinding("transport_scope_exceeds_identity")
        if not self.allowed_capabilities <= self.identity.capabilities:
            raise InvalidTrustBinding("capability_scope_exceeds_identity")
        if self.state_changed_at.tzinfo is None or self.state_changed_at.utcoffset() is None:
            raise InvalidTrustBinding("invalid_state_changed_at")
        object.__setattr__(self, "state_changed_at", self.state_changed_at.astimezone(timezone.utc))

    def to_mapping(self) -> dict[str, object]:
        return {
            "identity": self.identity.to_mapping(),
            "trust_state": self.trust_state.value,
            "allowed_transports": sorted(self.allowed_transports),
            "allowed_capabilities": sorted(self.allowed_capabilities),
            "state_changed_at": self.state_changed_at.isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True)
class TrustDecision:
    status: str
    machine_id: str
    key_id: str | None
    key_version: int | None
    trust_state: str | None
    reasons: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return self.status == "allowed"

    def to_receipt(self) -> dict[str, object]:
        return {
            "status": self.status,
            "machine_id": self.machine_id,
            "key_id": self.key_id,
            "key_version": self.key_version,
            "trust_state": self.trust_state,
            "reasons": list(self.reasons),
        }


class TrustRegistry:
    def __init__(
        self,
        bindings: Iterable[TrustBinding],
        *,
        max_verification_age: timedelta = DEFAULT_MAX_VERIFICATION_AGE,
    ) -> None:
        if max_verification_age <= timedelta(0):
            raise InvalidTrustBinding("invalid_max_verification_age")
        self._max_verification_age = max_verification_age
        by_machine: dict[str, dict[str, TrustBinding]] = {}
        fingerprints: dict[str, set[str]] = {}
        for binding in bindings:
            if not isinstance(binding, TrustBinding):
                raise InvalidTrustBinding("invalid_binding")
            machine = binding.identity.machine_id
            machine_bindings = by_machine.setdefault(machine, {})
            if binding.identity.key_id in machine_bindings:
                raise InvalidTrustBinding("duplicate_machine_key")
            machine_fingerprints = fingerprints.setdefault(machine, set())
            if binding.identity.public_fingerprint in machine_fingerprints:
                raise InvalidTrustBinding("duplicate_machine_fingerprint")
            machine_bindings[binding.identity.key_id] = binding
            machine_fingerprints.add(binding.identity.public_fingerprint)
        self._bindings = by_machine

    def authorize(
        self,
        *,
        machine_id: str,
        public_fingerprint: str,
        transport_profile: str,
        capability: str,
        at: datetime | None = None,
    ) -> TrustDecision:
        if at is not None and (at.tzinfo is None or at.utcoffset() is None):
            raise InvalidTrustBinding("invalid_authorization_time")
        now = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        candidates = self._bindings.get(machine_id)
        if not candidates:
            return self._blocked(machine_id, None, "UNKNOWN_IDENTITY")
        binding = next(
            (item for item in candidates.values() if item.identity.public_fingerprint == public_fingerprint),
            None,
        )
        if binding is None:
            return self._blocked(machine_id, None, "FINGERPRINT_MISMATCH")

        reasons: list[str] = []
        identity = binding.identity
        if binding.trust_state == TrustState.PENDING:
            reasons.append("IDENTITY_PENDING")
        elif binding.trust_state == TrustState.REVOKED:
            reasons.append("IDENTITY_REVOKED")
        elif binding.trust_state not in {TrustState.TRUSTED, TrustState.ROTATING}:
            reasons.append("IDENTITY_NOT_TRUSTED")
        if now < identity.issued_at:
            reasons.append("IDENTITY_NOT_YET_VALID")
        if identity.expires_at is not None and now >= identity.expires_at:
            reasons.append("IDENTITY_EXPIRED")
        if identity.last_verified_at > now:
            reasons.append("VERIFICATION_TIMESTAMP_IN_FUTURE")
        elif now - identity.last_verified_at > self._max_verification_age:
            reasons.append("IDENTITY_STALE")
        if transport_profile not in binding.allowed_transports:
            reasons.append("TRANSPORT_NOT_ALLOWED")
        if capability not in binding.allowed_capabilities:
            reasons.append("CAPABILITY_NOT_ALLOWED")
        if reasons:
            return TrustDecision(
                status="blocked",
                machine_id=machine_id,
                key_id=identity.key_id,
                key_version=identity.key_version,
                trust_state=binding.trust_state.value,
                reasons=tuple(reasons),
            )
        return TrustDecision(
            status="allowed",
            machine_id=machine_id,
            key_id=identity.key_id,
            key_version=identity.key_version,
            trust_state=binding.trust_state.value,
            reasons=(),
        )

    def with_state(
        self,
        *,
        machine_id: str,
        key_id: str,
        trust_state: TrustState,
        changed_at: datetime,
    ) -> "TrustRegistry":
        machine = self._bindings.get(machine_id)
        if not machine or key_id not in machine:
            raise InvalidTrustBinding("unknown_machine_key")
        if changed_at.tzinfo is None or changed_at.utcoffset() is None:
            raise InvalidTrustBinding("invalid_state_change_time")
        updated: list[TrustBinding] = []
        for current_machine in self._bindings.values():
            for binding in current_machine.values():
                if binding.identity.machine_id == machine_id and binding.identity.key_id == key_id:
                    updated.append(replace(binding, trust_state=trust_state, state_changed_at=changed_at))
                else:
                    updated.append(binding)
        return TrustRegistry(updated, max_verification_age=self._max_verification_age)

    def public_snapshot(self) -> tuple[Mapping[str, object], ...]:
        rows = [binding.to_mapping() for machine in self._bindings.values() for binding in machine.values()]

        def sort_key(row: Mapping[str, object]) -> tuple[str, str]:
            identity = row.get("identity")
            if not isinstance(identity, Mapping):
                raise InvalidTrustBinding("invalid_public_snapshot")
            return str(identity.get("machine_id")), str(identity.get("key_id"))

        return tuple(sorted(rows, key=sort_key))

    @staticmethod
    def _blocked(machine_id: str, binding: TrustBinding | None, reason: str) -> TrustDecision:
        return TrustDecision(
            status="blocked",
            machine_id=machine_id,
            key_id=None if binding is None else binding.identity.key_id,
            key_version=None if binding is None else binding.identity.key_version,
            trust_state=None if binding is None else binding.trust_state.value,
            reasons=(reason,),
        )
