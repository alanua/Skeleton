from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.scheduler_models import ScheduleSpec
from core.scheduler_store import SchedulerStore


@dataclass(frozen=True)
class MailSchedulerReceipt:
    schedule_id: str
    created: bool


class MailScheduler:
    def register_deadline_checkpoint(
        self, checkpoint: Mapping[str, Any] | ScheduleSpec, *, now: int
    ) -> MailSchedulerReceipt:
        raise NotImplementedError


class SchedulerStoreMailScheduler(MailScheduler):
    def __init__(self, db_path: str | Path) -> None:
        self.store = SchedulerStore(db_path)

    def register_deadline_checkpoint(
        self, checkpoint: Mapping[str, Any] | ScheduleSpec, *, now: int
    ) -> MailSchedulerReceipt:
        spec = checkpoint if isinstance(checkpoint, ScheduleSpec) else ScheduleSpec.from_mapping(checkpoint)
        self.store.initialize()
        _record, created = self.store.register(spec, now=now, enabled=True)
        return MailSchedulerReceipt(schedule_id=spec.schedule_id, created=created)


class InMemoryMailScheduler(MailScheduler):
    def __init__(self) -> None:
        self.checkpoints: dict[str, Mapping[str, Any]] = {}

    def register_deadline_checkpoint(
        self, checkpoint: Mapping[str, Any] | ScheduleSpec, *, now: int
    ) -> MailSchedulerReceipt:
        mapping = checkpoint.to_mapping() if isinstance(checkpoint, ScheduleSpec) else dict(checkpoint)
        schedule_id = str(mapping["schedule_id"])
        created = schedule_id not in self.checkpoints
        self.checkpoints.setdefault(schedule_id, mapping)
        return MailSchedulerReceipt(schedule_id=schedule_id, created=created)
