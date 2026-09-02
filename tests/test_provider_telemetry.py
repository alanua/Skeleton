from __future__ import annotations

from core.provider_registry import ProviderHealth
from core.provider_telemetry import (
    MAX_COOLDOWN_ATTEMPTS,
    MAX_FAILURES,
    ProviderHealthRecord,
    record_failure,
    record_outage,
    record_success,
    tick_cooldown,
)


def test_health_backoff_is_deterministic_and_bounded() -> None:
    record = ProviderHealthRecord("cloud-primary")

    for _ in range(10):
        record = record_failure(record)

    assert record.health == ProviderHealth.COOLDOWN
    assert record.consecutive_failures == MAX_FAILURES
    assert record.cooldown_attempts_remaining == MAX_COOLDOWN_ATTEMPTS


def test_cooldown_ticks_to_degraded_not_live() -> None:
    record = record_failure(record_failure(record_failure(ProviderHealthRecord("cloud-primary"))))

    for _ in range(MAX_COOLDOWN_ATTEMPTS):
        record = tick_cooldown(record)

    assert record.health == ProviderHealth.DEGRADED
    assert record.cooldown_attempts_remaining == 0


def test_success_resets_health() -> None:
    record = record_outage(ProviderHealthRecord("cloud-primary"))
    assert record.health == ProviderHealth.OUTAGE

    record = record_success(record)

    assert record.health == ProviderHealth.LIVE
    assert record.consecutive_failures == 0
    assert record.cooldown_attempts_remaining == 0
