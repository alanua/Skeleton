from datetime import datetime, timezone

import pytest

from core.scheduler_models import (
    ScheduleSpec,
    SchedulerValidationError,
    iter_due_times,
    stable_occurrence_id,
)


def _cron(**overrides):
    value = {
        "schema": "skeleton.schedule.v1",
        "schedule_id": "test.cron",
        "trigger_kind": "cron",
        "cron_expression": "*/5 * * * *",
        "once_at": None,
        "timezone": "Europe/Berlin",
        "route_type": "notify",
        "route_id": "test.notice",
        "approval_policy": "notify_only",
        "overlap_policy": "skip",
        "misfire_policy": "run_once",
        "payload": {"message": "private"},
    }
    value.update(overrides)
    return value


def test_cron_due_times_use_schedule_timezone() -> None:
    spec = ScheduleSpec.from_mapping(_cron(cron_expression="0 8 * * *"))
    start = int(datetime(2026, 7, 28, 5, 59, tzinfo=timezone.utc).timestamp())
    end = int(datetime(2026, 7, 28, 6, 1, tzinfo=timezone.utc).timestamp())
    assert iter_due_times(spec, after_exclusive=start, until_inclusive=end) == (
        int(datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc).timestamp()),
    )


def test_cron_supports_ranges_lists_and_steps() -> None:
    spec = ScheduleSpec.from_mapping(
        _cron(cron_expression="0,30 8-10/2 * * 1-5", timezone="UTC")
    )
    start = int(datetime(2026, 7, 28, 7, 59, tzinfo=timezone.utc).timestamp())
    end = int(datetime(2026, 7, 28, 10, 31, tzinfo=timezone.utc).timestamp())
    assert iter_due_times(spec, after_exclusive=start, until_inclusive=end) == (
        int(datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 7, 28, 8, 30, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 7, 28, 10, 30, tzinfo=timezone.utc).timestamp()),
    )


def test_occurrence_identity_is_stable_and_versioned() -> None:
    first = stable_occurrence_id("test.cron", 1, 100)
    assert first == stable_occurrence_id("test.cron", 1, 100)
    assert first != stable_occurrence_id("test.cron", 2, 100)
    assert first.startswith("occ_")


def test_trigger_fields_fail_closed() -> None:
    with pytest.raises(SchedulerValidationError) as exc:
        ScheduleSpec.from_mapping(_cron(once_at=123))
    assert exc.value.reason_code == "INVALID_TRIGGER_FIELDS"


def test_invalid_timezone_fails_closed() -> None:
    with pytest.raises(SchedulerValidationError) as exc:
        ScheduleSpec.from_mapping(_cron(timezone="Mars/Olympus"))
    assert exc.value.reason_code == "INVALID_TIMEZONE"
