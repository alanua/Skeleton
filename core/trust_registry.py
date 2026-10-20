from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable, Mapping

from core.machine_identity import MachineIdentity, _require_token


DEFAULT_MAX_VERIFICATION_AGE = timedelta(days=30)
CONSTRAINED_RELAY_NODE_CLASSES = frozenset({"constrained_relay", "device_relay"})
ELEVATED_CAPABILITY_SUFFIXES = (".control", ".admin", ".write", ":control", ":admin", ":write")


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
    rotation_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, MachineIdentity):
            raise InvalidTrustBinding("invalid_identity")
        try:
            state = self.trust_state if isinstance(self.trust_state, TrustState) else TrustState(self.trust_state)
        except (TypeError, ValueError) as exc:
            raise InvalidTrustBinding("invalid_trust_state") from exc
        object.__setattr__(self, "trust_state", state)
        if not isinstance(self.allowed_transports, (tuple, list, set, frozenset)) or not self.allowed_transports:
            raise InvalidTrustBinding("invalid_allowed_transports")
        if not isinstance(self.allowed_capabilities, (tuple, list, set, frozenset)) or not self.allowed_capabilities:
            raise InvalidTrustBinding("invalid_allowed_capabilities")
        allowed_transports = frozenset(_require_token("allowed_transports", value) for value in self.allowed_transports)
        allowed_capabilities = frozenset(
            _require_token("allowed_capabilities", value) for value in self.allowed_capabilities
        )
        if len(allowed_transports) != len(self.allowed_transports):
            raise InvalidTrustBinding("duplicate_allowed_transports")
        if len(allowed_capabilities) != len(self.allowed_capabilities):
            raise InvalidTrustBinding("duplicate_allowed_capabilities")
        object.__setattr__(self, "allowed_transports", allowed_transports)
        object.__setattr__(self, "allowed_capabilities", allowed_capabilities)
        if not self.allowed_transports <= self.identity.transport_profiles:
            raise InvalidTrustBinding("transport_scope_exceeds_identity")
        if not self.allowed_capabilities <= self.identity.capabilities:
            raise InvalidTrustBinding("capability_scope_exceeds_identity")
        if self.identity.node_class in CONSTRAINED_RELAY_NODE_CLASSES and any(
            capability.endswith(ELEVATED_CAPABILITY_SUFFIXES) for capability in self.allowed_capabilities
        ):
            raise InvalidTrustBinding("constrained_relay_capability_too_broad")
        if self.state_changed_at.tzinfo is None or self.state_changed_at.utcoffset() is None:
            raise InvalidTrustBinding("invalid_state_changed_at")
        changed_at = self.state_changed_at.astimezone(timezone.utc)
        rotation_expires_at = self.rotation_expires_at
        if self.trust_state == TrustState.ROTATING:
            if rotation_expires_at is None:
                raise InvalidTrustBinding("missing_rotation_expires_at")
            if rotation_expires_at.tzinfo is None or rotation_expires_at.utcoffset() is None:
                raise InvalidTrustBinding("invalid_rotation_expires_at")
            rotation_expires_at = rotation_expires_at.astimezone(timezone.utc)
            if rotation_expires_at <= changed_at:
                raise InvalidTrustBinding("invalid_rotation_window")
        elif rotation_expires_at is not None:
            raise InvalidTrustBinding("unexpected_rotation_expires_at")
        object.__setattr__(self, "state_changed_at", changed_at)
        object.__setattr__(self, "rotation_expires_at", rotation_expires_at)

    def to_mapping(self) -> dict[str, object]:
        return {
            "identity": self.identity.to_mapping(),
            "trust_state": self.trust_state.value,
            "allowed_transports": sorted(self.allowed_transports),
            "allowed_capabilities": sorted(self.allowed_capabilities),
            "state_changed_at": self.state_changed_at.isoformat().replace("+00:00", "Z"),
            "rotation_expires_at": None
            if self.rotation_expires_at is None
            else self.rotation_expires_at.isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True)
class TrustDecision:
    status: str
    machine_id: str
    public_fingerprint: str | None
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
            "public_fingerprint": self.public_fingerprint,
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
        fingerprints: dict[str, str] = {}
        versions: dict[str, set[int]] = {}
        for binding in bindings:
            if not isinstance(binding, TrustBinding):
                raise InvalidTrustBinding("invalid_binding")
            machine = binding.identity.machine_id
            machine_bindings = by_machine.setdefault(machine, {})
            if binding.identity.key_id in machine_bindings:
                raise InvalidTrustBinding("duplicate_machine_key")
            machine_versions = versions.setdefault(machine, set())
            if binding.identity.key_version in machine_versions:
                raise InvalidTrustBinding("duplicate_machine_key_version")
            fingerprint_owner = fingerprints.get(binding.identity.public_fingerprint)
            if fingerprint_owner is not None:
                if fingerprint_owner == machine:
                    raise InvalidTrustBinding("duplicate_machine_fingerprint")
                raise InvalidTrustBinding("duplicate_public_fingerprint")
            machine_bindings[binding.identity.key_id] = binding
            machine_versions.add(binding.identity.key_version)
            fingerprints[binding.identity.public_fingerprint] = machine
        for machine, machine_bindings in by_machine.items():
            trusted_versions = {
                binding.identity.key_version
                for binding in machine_bindings.values()
                if binding.trust_state == TrustState.TRUSTED
            }
            for binding in machine_bindings.values():
                if binding.trust_state == TrustState.ROTATING and not any(
                    version > binding.identity.key_version for version in trusted_versions
                ):
                    raise InvalidTrustBinding("rotation_without_trusted_successor")
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
        if (
            binding.trust_state == TrustState.ROTATING
            and binding.rotation_expires_at is not None
            and now >= binding.rotation_expires_at
        ):
            reasons.append("ROTATION_OVERLAP_EXPIRED")
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
                public_fingerprint=identity.public_fingerprint,
                key_id=identity.key_id,
                key_version=identity.key_version,
                trust_state=binding.trust_state.value,
                reasons=tuple(reasons),
            )
        return TrustDecision(
            status="allowed",
            machine_id=machine_id,
            public_fingerprint=identity.public_fingerprint,
            key_id=identity.key_id,
            key_version=identity.key_version,
            trust_state=binding.trust_state.value,
            reasons=(),
        )

    def enrollment_state(
        self,
        *,
        machine_id: str,
        public_fingerprint: str,
        at: datetime | None = None,
    ) -> TrustDecision:
        if at is not None and (at.tzinfo is None or at.utcoffset() is None):
            raise InvalidTrustBinding("invalid_enrollment_time")
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
        if (
            binding.trust_state == TrustState.ROTATING
            and binding.rotation_expires_at is not None
            and now >= binding.rotation_expires_at
        ):
            reasons.append("ROTATION_OVERLAP_EXPIRED")
        if now < identity.issued_at:
            reasons.append("IDENTITY_NOT_YET_VALID")
        if identity.expires_at is not None and now >= identity.expires_at:
            reasons.append("IDENTITY_EXPIRED")
        if identity.last_verified_at > now:
            reasons.append("VERIFICATION_TIMESTAMP_IN_FUTURE")
        elif now - identity.last_verified_at > self._max_verification_age:
            reasons.append("IDENTITY_STALE")
        return TrustDecision(
            status="blocked" if reasons else "allowed",
            machine_id=machine_id,
            public_fingerprint=identity.public_fingerprint,
            key_id=identity.key_id,
            key_version=identity.key_version,
            trust_state=binding.trust_state.value,
            reasons=tuple(reasons),
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
                    rotation_expires_at = (
                        binding.rotation_expires_at if TrustState(trust_state) == TrustState.ROTATING else None
                    )
                    updated.append(
                        replace(
                            binding,
                            trust_state=trust_state,
                            state_changed_at=changed_at,
                            rotation_expires_at=rotation_expires_at,
                        )
                    )
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
            public_fingerprint=None if binding is None else binding.identity.public_fingerprint,
            key_id=None if binding is None else binding.identity.key_id,
            key_version=None if binding is None else binding.identity.key_version,
            trust_state=None if binding is None else binding.trust_state.value,
            reasons=(reason,),
        )
