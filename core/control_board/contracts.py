from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Final, Literal


SNAPSHOT_SCHEMA: Final = "skeleton.control_board.snapshot.v1"
MAX_TEXT_LENGTH: Final = 180
MAX_LONG_TEXT_LENGTH: Final = 480
MAX_ITEMS: Final = 24

Status = Literal["ok", "watch", "blocked", "review", "queued", "running", "done"]


class ControlBoardValidationError(ValueError):
    """Raised when a synthetic control board snapshot is malformed."""


@dataclass(frozen=True)
class Metric:
    label: str
    value: str
    tone: Status


@dataclass(frozen=True)
class TodayItem:
    title: str
    detail: str
    status: Status
    owner: str


@dataclass(frozen=True)
class KanbanCard:
    title: str
    detail: str
    lane: str
    status: Status


@dataclass(frozen=True)
class ProjectItem:
    name: str
    summary: str
    status: Status
    updated: str


@dataclass(frozen=True)
class ApprovalItem:
    request: str
    source: str
    status: Status


@dataclass(frozen=True)
class WorkflowItem:
    name: str
    stage: str
    status: Status
    next_step: str


@dataclass(frozen=True)
class EvidenceItem:
    label: str
    reference: str
    status: Status


@dataclass(frozen=True)
class HealthItem:
    component: str
    status: Status
    detail: str


@dataclass(frozen=True)
class ControlBoardSnapshot:
    schema: str
    generated_at: str
    title: str
    subtitle: str
    metrics: tuple[Metric, ...]
    today: tuple[TodayItem, ...]
    kanban: tuple[KanbanCard, ...]
    projects: tuple[ProjectItem, ...]
    approvals: tuple[ApprovalItem, ...]
    workflows: tuple[WorkflowItem, ...]
    evidence: tuple[EvidenceItem, ...]
    health: tuple[HealthItem, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ControlBoardSnapshot:
        if not isinstance(value, Mapping):
            raise ControlBoardValidationError("snapshot must be an object")
        schema = _exact(value, "schema", SNAPSHOT_SCHEMA)
        return cls(
            schema=schema,
            generated_at=_text(value, "generated_at"),
            title=_text(value, "title"),
            subtitle=_text(value, "subtitle", max_length=MAX_LONG_TEXT_LENGTH),
            metrics=_items(value, "metrics", _metric),
            today=_items(value, "today", _today),
            kanban=_items(value, "kanban", _kanban),
            projects=_items(value, "projects", _project),
            approvals=_items(value, "approvals", _approval),
            workflows=_items(value, "workflows", _workflow),
            evidence=_items(value, "evidence", _evidence),
            health=_items(value, "health", _health),
        )

    @classmethod
    def from_json(cls, value: str) -> ControlBoardSnapshot:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ControlBoardValidationError("snapshot JSON is invalid") from exc
        return cls.from_mapping(parsed)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "generated_at": self.generated_at,
            "title": self.title,
            "subtitle": self.subtitle,
            "metrics": [item.__dict__ for item in self.metrics],
            "today": [item.__dict__ for item in self.today],
            "kanban": [item.__dict__ for item in self.kanban],
            "projects": [item.__dict__ for item in self.projects],
            "approvals": [item.__dict__ for item in self.approvals],
            "workflows": [item.__dict__ for item in self.workflows],
            "evidence": [item.__dict__ for item in self.evidence],
            "health": [item.__dict__ for item in self.health],
        }


def load_snapshot(path: Path) -> ControlBoardSnapshot:
    return ControlBoardSnapshot.from_json(path.read_text(encoding="utf-8"))


def _metric(value: Mapping[str, Any]) -> Metric:
    return Metric(label=_text(value, "label"), value=_text(value, "value"), tone=_status(value, "tone"))


def _today(value: Mapping[str, Any]) -> TodayItem:
    return TodayItem(
        title=_text(value, "title"),
        detail=_text(value, "detail", max_length=MAX_LONG_TEXT_LENGTH),
        status=_status(value, "status"),
        owner=_text(value, "owner"),
    )


def _kanban(value: Mapping[str, Any]) -> KanbanCard:
    return KanbanCard(
        title=_text(value, "title"),
        detail=_text(value, "detail", max_length=MAX_LONG_TEXT_LENGTH),
        lane=_text(value, "lane"),
        status=_status(value, "status"),
    )


def _project(value: Mapping[str, Any]) -> ProjectItem:
    return ProjectItem(
        name=_text(value, "name"),
        summary=_text(value, "summary", max_length=MAX_LONG_TEXT_LENGTH),
        status=_status(value, "status"),
        updated=_text(value, "updated"),
    )


def _approval(value: Mapping[str, Any]) -> ApprovalItem:
    return ApprovalItem(
        request=_text(value, "request", max_length=MAX_LONG_TEXT_LENGTH),
        source=_text(value, "source"),
        status=_status(value, "status"),
    )


def _workflow(value: Mapping[str, Any]) -> WorkflowItem:
    return WorkflowItem(
        name=_text(value, "name"),
        stage=_text(value, "stage"),
        status=_status(value, "status"),
        next_step=_text(value, "next_step", max_length=MAX_LONG_TEXT_LENGTH),
    )


def _evidence(value: Mapping[str, Any]) -> EvidenceItem:
    return EvidenceItem(
        label=_text(value, "label"),
        reference=_text(value, "reference", max_length=MAX_LONG_TEXT_LENGTH),
        status=_status(value, "status"),
    )


def _health(value: Mapping[str, Any]) -> HealthItem:
    return HealthItem(
        component=_text(value, "component"),
        status=_status(value, "status"),
        detail=_text(value, "detail", max_length=MAX_LONG_TEXT_LENGTH),
    )


def _items(value: Mapping[str, Any], field: str, parser: Any) -> tuple[Any, ...]:
    raw = value.get(field)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise ControlBoardValidationError(f"{field} must be an array")
    if not raw or len(raw) > MAX_ITEMS:
        raise ControlBoardValidationError(f"{field} must contain 1-{MAX_ITEMS} items")
    parsed = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ControlBoardValidationError(f"{field} items must be objects")
        parsed.append(parser(item))
    return tuple(parsed)


def _exact(value: Mapping[str, Any], field: str, expected: str) -> str:
    actual = _text(value, field)
    if actual != expected:
        raise ControlBoardValidationError(f"{field} must be {expected}")
    return actual


def _status(value: Mapping[str, Any], field: str) -> Status:
    status = _text(value, field)
    if status not in {"ok", "watch", "blocked", "review", "queued", "running", "done"}:
        raise ControlBoardValidationError(f"{field} has unsupported status")
    return status  # type: ignore[return-value]


def _text(value: Mapping[str, Any], field: str, *, max_length: int = MAX_TEXT_LENGTH) -> str:
    raw = value.get(field)
    if not isinstance(raw, str):
        raise ControlBoardValidationError(f"{field} must be a string")
    text = raw.strip()
    if not text or len(text) > max_length:
        raise ControlBoardValidationError(f"{field} must be 1-{max_length} characters")
    return text
