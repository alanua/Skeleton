from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence


LIVE_STATE_SCHEMA = "skeleton.operator_live_state.public.v1"
STALE_AFTER_SECONDS = 5 * 60
RECENT_DONE_LIMIT = 5
ACTIVE_LIMIT = 8

_RAW_REF_RE = re.compile(
    r"(?i)(?:\b(?:issue|pr|runner)\s*#?\d+\b|#\d+\b|\b[0-9a-f]{7,40}\b|\bsha\b|\bgithub\b)"
)


@dataclass(frozen=True)
class OperatorLiveItem:
    title: str
    detail: str
    updated_at: int
    drilldown: Mapping[str, str] = field(default_factory=dict)

    def public_mapping(self, *, include_drilldown: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "title": self.title,
            "detail": self.detail,
            "updated_at": self.updated_at,
        }
        if include_drilldown:
            value["drilldown"] = dict(self.drilldown)
        return value


@dataclass(frozen=True)
class OperatorLiveState:
    status: str
    refreshed_at: int | None
    stale: bool
    running: tuple[OperatorLiveItem, ...] = ()
    waiting: tuple[OperatorLiveItem, ...] = ()
    needs_attention: tuple[OperatorLiveItem, ...] = ()
    recently_done: tuple[OperatorLiveItem, ...] = ()
    next_items: tuple[OperatorLiveItem, ...] = ()
    message: str = ""

    def public_mapping(self, *, include_drilldown: bool = False) -> dict[str, Any]:
        return {
            "schema": LIVE_STATE_SCHEMA,
            "status": self.status,
            "stale": self.stale,
            "refreshed_at": self.refreshed_at,
            "message": self.message,
            "sections": {
                "Працює зараз": [
                    item.public_mapping(include_drilldown=include_drilldown)
                    for item in self.running
                ],
                "Чекає": [
                    item.public_mapping(include_drilldown=include_drilldown)
                    for item in self.waiting
                ],
                "Потрібна моя увага": [
                    item.public_mapping(include_drilldown=include_drilldown)
                    for item in self.needs_attention
                ],
                "Щойно завершено": [
                    item.public_mapping(include_drilldown=include_drilldown)
                    for item in self.recently_done
                ],
                "Далі": [
                    item.public_mapping(include_drilldown=include_drilldown)
                    for item in self.next_items
                ],
            },
        }


def load_operator_live_state(
    runner_queue_snapshot: Mapping[str, Any],
    scheduler_db_path: str | Path,
    *,
    now: int | None = None,
    include_drilldown: bool = False,
) -> dict[str, Any]:
    state = read_operator_live_state(runner_queue_snapshot, scheduler_db_path, now=now)
    return state.public_mapping(include_drilldown=include_drilldown)


def stale_operator_live_state(
    *,
    refreshed_at: int | None = None,
    message: str = "Живий стан тимчасово недоступний.",
) -> dict[str, Any]:
    return OperatorLiveState(
        status="offline",
        refreshed_at=refreshed_at,
        stale=True,
        message=message,
    ).public_mapping()


def read_operator_live_state(
    runner_queue_snapshot: Mapping[str, Any],
    scheduler_db_path: str | Path,
    *,
    now: int | None = None,
) -> OperatorLiveState:
    current = _timestamp(now)
    issues, generated_at = _runner_queue_issues(runner_queue_snapshot)
    supplements = _scheduler_supplements(scheduler_db_path)

    if not issues and current - generated_at > STALE_AFTER_SECONDS:
        return OperatorLiveState(
            status="stale",
            refreshed_at=current,
            stale=True,
            message="Стан черги застарів.",
        )

    return _state_from_queue_issues(issues, supplements, generated_at, current)


def _scheduler_supplements(scheduler_db_path: str | Path) -> dict[int, sqlite3.Row]:
    db_path = Path(scheduler_db_path)
    if not db_path.exists():
        return {}
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT occurrence_id, schedule_id, schedule_version, scheduled_for, state,
                   reason, proposal_json, created_at, updated_at, started_at,
                   attempt, idempotency_key, parent_occurrence_id, parent_receipt_id
              FROM occurrences
             ORDER BY updated_at DESC, scheduled_for ASC, occurrence_id ASC
            """
        ).fetchall()

    supplements: dict[int, sqlite3.Row] = {}
    for row in rows:
        issue_number = _scheduler_issue_number(row)
        if issue_number is not None and issue_number not in supplements:
            supplements[issue_number] = row
    return supplements


def _state_from_queue_issues(
    issues: Sequence[Mapping[str, Any]],
    supplements: Mapping[int, sqlite3.Row],
    generated_at: int,
    current: int,
) -> OperatorLiveState:
    running: list[OperatorLiveItem] = []
    waiting: list[OperatorLiveItem] = []
    needs_attention: list[OperatorLiveItem] = []
    recently_done: list[OperatorLiveItem] = []
    next_items: list[OperatorLiveItem] = []

    for issue in issues:
        issue_number = _issue_number(issue)
        labels = _labels(issue)
        item = _issue_to_item(issue, supplements.get(issue_number))
        if "runner:running" in labels:
            running.append(item)
        elif labels & {"runner:ready", "queue:RUN_NOW", "runner:waiting-dependency"}:
            waiting.append(item)
        elif "runner:blocked" in labels:
            needs_attention.append(item)
        elif "runner:done" in labels:
            recently_done.append(item)

    running = _limit_active(running)
    waiting = _limit_active(waiting)
    needs_attention = _limit_active(needs_attention)
    recently_done = recently_done[:RECENT_DONE_LIMIT]
    if not running and waiting:
        next_items = waiting[:ACTIVE_LIMIT]
    else:
        next_items = waiting[: min(3, ACTIVE_LIMIT)]

    stale = current - generated_at > STALE_AFTER_SECONDS
    return OperatorLiveState(
        status="stale" if stale else "online",
        refreshed_at=current,
        stale=stale,
        running=tuple(running),
        waiting=tuple(waiting),
        needs_attention=tuple(needs_attention),
        recently_done=tuple(recently_done),
        next_items=tuple(next_items),
        message="Стан черги застарів." if stale else "Живий стан оновлено.",
    )


def _issue_to_item(issue: Mapping[str, Any], supplement: sqlite3.Row | None) -> OperatorLiveItem:
    title = _public_text(_first_text(issue.get("operator_title"), issue.get("title")), fallback="Завдання")
    detail = _public_text(
        _first_text(
            issue.get("operator_detail"),
            issue.get("status"),
            _supplement_detail(supplement),
            "Стан черги оновлено.",
        ),
        fallback="Стан черги оновлено.",
    )
    updated_at = max(_issue_updated_at(issue), _supplement_updated_at(supplement))
    drilldown = {"issue_number": str(_issue_number(issue))}
    if supplement is not None:
        drilldown["occurrence_id"] = str(supplement["occurrence_id"])
        drilldown["schedule_id"] = str(supplement["schedule_id"])
        drilldown["scheduler_state"] = str(supplement["state"])
    return OperatorLiveItem(
        title=title,
        detail=detail,
        updated_at=updated_at,
        drilldown=drilldown,
    )


def _scheduler_issue_number(row: sqlite3.Row) -> int | None:
    proposal = _proposal(row["proposal_json"])
    payload = proposal.get("payload") if isinstance(proposal.get("payload"), Mapping) else {}
    assert isinstance(payload, Mapping)
    for value in (
        payload.get("issue_number"),
        payload.get("source_issue_number"),
        proposal.get("issue_number"),
    ):
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        if isinstance(value, str) and re.fullmatch(r"[1-9]\d*", value):
            return int(value)
    for value in (row["idempotency_key"], row["schedule_id"], row["occurrence_id"]):
        match = re.search(r"issue[-_:.]?([1-9]\d*)", str(value), re.I)
        if match:
            return int(match.group(1))
    return None


def _runner_queue_issues(snapshot: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], int]:
    if not isinstance(snapshot, Mapping):
        raise ValueError("runner queue snapshot must be a mapping")
    generated_at = snapshot.get("generated_at")
    if not isinstance(generated_at, int) or isinstance(generated_at, bool) or generated_at < 0:
        raise ValueError("runner queue snapshot generated_at must be a non-negative integer")
    issues = snapshot.get("issues")
    if not isinstance(issues, list):
        raise ValueError("runner queue snapshot issues must be a list")
    normalized: list[Mapping[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, Mapping):
            raise ValueError("runner queue issue must be a mapping")
        _issue_number(issue)
        _labels(issue)
        _issue_updated_at(issue)
        if not isinstance(issue.get("title"), str):
            raise ValueError("runner queue issue title must be a string")
        normalized.append(issue)
    return tuple(normalized), generated_at


def _issue_number(issue: Mapping[str, Any]) -> int:
    number = issue.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
        raise ValueError("runner queue issue number must be a positive integer")
    return number


def _labels(issue: Mapping[str, Any]) -> frozenset[str]:
    labels = issue.get("labels")
    if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
        raise ValueError("runner queue issue labels must be a string list")
    return frozenset(labels)


def _issue_updated_at(issue: Mapping[str, Any]) -> int:
    updated_at = issue.get("updated_at")
    if not isinstance(updated_at, int) or isinstance(updated_at, bool) or updated_at < 0:
        raise ValueError("runner queue issue updated_at must be a non-negative integer")
    return updated_at


def _supplement_updated_at(row: sqlite3.Row | None) -> int:
    if row is None:
        return 0
    return int(row["updated_at"])


def _supplement_detail(row: sqlite3.Row | None) -> str:
    if row is None:
        return ""
    return _state_detail(str(row["state"]), str(row["reason"]))


def _proposal(value: object) -> Mapping[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _state_detail(state: str, reason: str) -> str:
    if state == "running":
        return "Виконується зараз."
    if state == "pending":
        return "Готово до наступного запуску."
    if state == "waiting_dependency":
        return "Чекає на попередній крок."
    if state == "needs_operator":
        return "Потрібне рішення оператора."
    if state == "failed":
        return "Зупинено через помилку."
    if state == "done":
        return "Завершено."
    return reason.replace("_", " ").lower() or "Стан оновлено."


def _public_text(value: str, *, fallback: str) -> str:
    text = " ".join(str(value or "").split())
    if not text or _RAW_REF_RE.search(text):
        return fallback
    return text[:140]


def _first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _timestamp(value: int | None) -> int:
    if value is None:
        return int(datetime.now(timezone.utc).timestamp())
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("now must be a non-negative integer")
    return value


def _limit_active(items: Sequence[OperatorLiveItem]) -> list[OperatorLiveItem]:
    return list(items[:ACTIVE_LIMIT])
