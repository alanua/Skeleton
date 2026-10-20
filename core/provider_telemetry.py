from __future__ import annotations

from dataclasses import dataclass

from core.provider_registry import ProviderHealth


class ProviderTelemetryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderHealthRecord:
    provider_alias: str
    health: ProviderHealth = ProviderHealth.LIVE
    consecutive_failures: int = 0
    cooldown_attempts_remaining: int = 0

    def __post_init__(self) -> None:
        if not self.provider_alias:
            raise ProviderTelemetryError("provider_alias_required")
        if self.consecutive_failures < 0:
            raise ProviderTelemetryError("negative_consecutive_failures")
        if self.cooldown_attempts_remaining < 0:
            raise ProviderTelemetryError("negative_cooldown")


MAX_FAILURES = 3
MAX_COOLDOWN_ATTEMPTS = 3


def record_success(record: ProviderHealthRecord) -> ProviderHealthRecord:
    return ProviderHealthRecord(
        provider_alias=record.provider_alias,
        health=ProviderHealth.LIVE,
        consecutive_failures=0,
        cooldown_attempts_remaining=0,
    )


def record_failure(record: ProviderHealthRecord) -> ProviderHealthRecord:
    failures = min(record.consecutive_failures + 1, MAX_FAILURES)
    if failures >= MAX_FAILURES:
        return ProviderHealthRecord(
            provider_alias=record.provider_alias,
            health=ProviderHealth.COOLDOWN,
            consecutive_failures=failures,
            cooldown_attempts_remaining=MAX_COOLDOWN_ATTEMPTS,
        )
    return ProviderHealthRecord(
        provider_alias=record.provider_alias,
        health=ProviderHealth.DEGRADED,
        consecutive_failures=failures,
        cooldown_attempts_remaining=0,
    )


def record_outage(record: ProviderHealthRecord) -> ProviderHealthRecord:
    return ProviderHealthRecord(
        provider_alias=record.provider_alias,
        health=ProviderHealth.OUTAGE,
        consecutive_failures=min(record.consecutive_failures + 1, MAX_FAILURES),
        cooldown_attempts_remaining=MAX_COOLDOWN_ATTEMPTS,
    )


def tick_cooldown(record: ProviderHealthRecord) -> ProviderHealthRecord:
    if record.health is not ProviderHealth.COOLDOWN:
        return record
    remaining = max(record.cooldown_attempts_remaining - 1, 0)
    if remaining:
        return ProviderHealthRecord(
            provider_alias=record.provider_alias,
            health=ProviderHealth.COOLDOWN,
            consecutive_failures=record.consecutive_failures,
            cooldown_attempts_remaining=remaining,
        )
    return ProviderHealthRecord(
        provider_alias=record.provider_alias,
        health=ProviderHealth.DEGRADED,
        consecutive_failures=record.consecutive_failures,
        cooldown_attempts_remaining=0,
    )
