from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.scheduler_models import ScheduleSpec
from core.scheduler_store import SchedulerStore


def register_mail_deadline_checkpoint(
    scheduler: SchedulerStore,
    checkpoint: ScheduleSpec | Mapping[str, Any],
    *,
    message_hash: str,
    now: int,
) -> bool:
    spec = checkpoint if isinstance(checkpoint, ScheduleSpec) else ScheduleSpec.from_mapping(checkpoint)
    record, created = scheduler.register(spec, now=now, enabled=True)
    return created and record.spec.schedule_id == spec.schedule_id and bool(message_hash)
