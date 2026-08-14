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
    scheduler_db_path: str | Path,
    *,
    now: int | None = None,
    include_drilldown: bool = False,
) -> dict[str, Any]:
    state = read_operator_live_state(scheduler_db_path, now=now)
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
    scheduler_db_path: str | Path,
    *,
    now: int | None = None,
) -> OperatorLiveState:
    current = _timestamp(now)
    db_path = Path(scheduler_db_path)
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

    running: list[OperatorLiveItem] = []
    waiting: list[OperatorLiveItem] = []
    needs_attention: list[OperatorLiveItem] = []
    recently_done: list[OperatorLiveItem] = []
    next_items: list[OperatorLiveItem] = []

    for row in rows:
        item = _row_to_item(row)
        state = str(row["state"])
        if state == "running":
            running.append(item)
        elif state in {"pending", "waiting_dependency"}:
            waiting.append(item)
        elif state in {"needs_operator", "failed"}:
            needs_attention.append(item)
        elif state == "done":
            recently_done.append(item)
        elif state == "skipped":
            continue

    running = _limit_active(running)
    waiting = _limit_active(waiting)
    needs_attention = _limit_active(needs_attention)
    recently_done = recently_done[:RECENT_DONE_LIMIT]
    if not running and waiting:
        next_items = waiting[:ACTIVE_LIMIT]
    else:
        next_items = waiting[: min(3, ACTIVE_LIMIT)]

    latest_update = max((int(row["updated_at"]) for row in rows), default=None)
    stale = latest_update is None or current - latest_update > STALE_AFTER_SECONDS
    return OperatorLiveState(
        status="stale" if stale else "online",
        refreshed_at=current,
        stale=stale,
        running=tuple(running),
        waiting=tuple(waiting),
        needs_attention=tuple(needs_attention),
        recently_done=tuple(recently_done),
        next_items=tuple(next_items),
        message="Стан застарів." if stale else "Живий стан оновлено.",
    )


def _row_to_item(row: sqlite3.Row) -> OperatorLiveItem:
    proposal = _proposal(row["proposal_json"])
    payload = proposal.get("payload") if isinstance(proposal.get("payload"), Mapping) else {}
    assert isinstance(payload, Mapping)
    title = _public_text(
        _first_text(
            payload.get("operator_title"),
            payload.get("title"),
            payload.get("summary"),
            proposal.get("route_id"),
            "Завдання",
        ),
        fallback="Завдання",
    )
    detail = _public_text(
        _first_text(
            payload.get("operator_detail"),
            payload.get("next_step"),
            _state_detail(str(row["state"]), str(row["reason"])),
        ),
        fallback=_state_detail(str(row["state"]), str(row["reason"])),
    )
    return OperatorLiveItem(
        title=title,
        detail=detail,
        updated_at=int(row["updated_at"]),
        drilldown={
            "occurrence_id": str(row["occurrence_id"]),
            "schedule_id": str(row["schedule_id"]),
            "state": str(row["state"]),
        },
    )


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
