from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

from core.operator_overview import (
    OperatorOverview,
    OperatorOverviewItem,
    OperatorOverviewStatus,
    build_operator_overview,
    load_operator_overview,
)


LIVE_STATE_SCHEMA = "skeleton.operator_live_state.v1"
_RAW_REF_RE = re.compile(r"(?i)(?:\b(?:issue|pr|task|runner)\s*#?\d+\b|#\d+\b|\b[0-9a-f]{7,40}\b)")
_RUNNER_LABEL_RE = re.compile(r"\b(?:runner/[A-Za-z0-9._/-]+|[a-z]+_[a-z0-9_]*:[A-Za-z0-9._/-]+)\b")
_LABELS_UK = {
    "active boot route": "Активний маршрут запуску",
    "boot loader": "Завантажувач",
    "helper registry": "Реєстр помічників",
    "write gate": "Гейт запису",
    "черга runner": "Черга роботи",
    "виконання runner у worktree": "Виконання роботи",
}
_GROUPS_UK = {
    "core/security": "ядрі безпеки",
    "autonomy/self-healing": "автономному виконанні",
    "memory/knowledge": "памʼяті та знаннях",
    "mail/documents/calendar": "пошті, документах і календарі",
    "Home/Home Edge": "Home та Home Edge",
    "AI executors": "AI-виконавцях",
    "domains/projects": "проєктах",
    "interfaces/control": "інтерфейсах керування",
}


@dataclass(frozen=True)
class OperatorLiveSection:
    title_uk: str
    empty_uk: str
    rows: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "title_uk": self.title_uk,
            "empty_uk": self.empty_uk,
            "rows": list(self.rows),
        }


@dataclass(frozen=True)
class OperatorLiveState:
    schema: str
    source_schema: str
    source_channel: str
    refreshed_at: str
    freshness: str
    sections: tuple[OperatorLiveSection, ...]
    drilldown: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "source_schema": self.source_schema,
            "source_channel": self.source_channel,
            "refreshed_at": self.refreshed_at,
            "freshness": self.freshness,
            "sections": [section.as_dict() for section in self.sections],
            "drilldown": self.drilldown,
        }


def load_operator_live_state(repo_root: str | Path = ".") -> OperatorLiveState:
    return operator_live_state_from_overview(load_operator_overview(repo_root))


def operator_live_state_from_overview(
    overview: OperatorOverview,
    *,
    refreshed_at: datetime | None = None,
    freshness: str = "current",
) -> OperatorLiveState:
    items = tuple(item for group in overview.groups for item in group.items)
    sections = (
        OperatorLiveSection(
            title_uk="Працює зараз",
            empty_uk="Немає підтвердженої активної роботи.",
            rows=_rows(
                _matching(items, {OperatorOverviewStatus.LIVE, OperatorOverviewStatus.PARTIAL}),
                focus="current",
                limit=4,
            ),
        ),
        OperatorLiveSection(
            title_uk="Будується зараз",
            empty_uk="Немає підтвердженого активного будівництва.",
            rows=_rows(_matching(items, {OperatorOverviewStatus.BUILDING}), focus="current", limit=3),
        ),
        OperatorLiveSection(
            title_uk="Очікує",
            empty_uk="Черга очікування порожня.",
            rows=_rows(_matching(items, {OperatorOverviewStatus.WAITING, OperatorOverviewStatus.PLANNED}), focus="next", limit=4),
        ),
        OperatorLiveSection(
            title_uk="Потрібна увага оператора",
            empty_uk="Немає явних пунктів для оператора.",
            rows=_rows(overview.needs_operator, focus="blocker", limit=4),
        ),
        OperatorLiveSection(
            title_uk="Щойно завершено",
            empty_uk="Немає нових підтверджених завершень.",
            rows=_rows(_matching(items, {OperatorOverviewStatus.LIVE, OperatorOverviewStatus.PARTIAL}), focus="finished", limit=4),
        ),
        OperatorLiveSection(
            title_uk="Далі",
            empty_uk="Наступний крок ще не визначено.",
            rows=(_plain_detail(overview.next_milestone, "next"),) if overview.next_milestone else (),
        ),
    )
    stamp = refreshed_at or datetime.now(UTC)
    return OperatorLiveState(
        schema=LIVE_STATE_SCHEMA,
        source_schema=overview.schema,
        source_channel="core.operator_overview.load_operator_overview",
        refreshed_at=stamp.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        freshness=freshness,
        sections=sections,
        drilldown={
            "generated_from": list(overview.generated_from),
            "item_count": len(items),
            "needs_operator_count": len(overview.needs_operator),
        },
    )


def _matching(
    items: Sequence[OperatorOverviewItem],
    statuses: set[OperatorOverviewStatus],
) -> tuple[OperatorOverviewItem, ...]:
    return tuple(item for item in items if item.status in statuses)


def _rows(items: Iterable[OperatorOverviewItem], *, focus: str, limit: int) -> tuple[str, ...]:
    rows: list[str] = []
    for item in items:
        if focus == "next":
            detail = item.next_milestone
        elif focus == "blocker":
            detail = item.blocker_plain
        elif focus == "finished":
            detail = "підтверджено в останньому зрізі."
        else:
            detail = item.current_focus or item.purpose
        rows.append(_public_safe(f"{_plain_item_label(item)}: {_plain_detail(detail, focus)}"))
        if len(rows) >= limit:
            break
    return tuple(rows)


def _public_safe(value: str) -> str:
    cleaned = _RAW_REF_RE.sub("технічне посилання", value)
    cleaned = cleaned.replace("технічний ref", "технічне посилання")
    cleaned = cleaned.replace("read-only", "лише читання")
    cleaned = cleaned.replace("Утримувати перевірений лише читання стан.", "Утримувати перевірений стан лише для читання.")
    cleaned = _RUNNER_LABEL_RE.sub("технічна мітка", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" :-")
    return cleaned or "Немає явного опису."


def _plain_item_label(item: OperatorOverviewItem) -> str:
    label = item.human_name.strip()
    mapped = _LABELS_UK.get(label.lower())
    if mapped:
        return mapped
    if not label or label.isdigit() or _englishish(label):
        return f"Пункт у {_GROUPS_UK.get(item.group, 'стані Skeleton')}"
    return label


def _plain_detail(value: str, focus: str) -> str:
    text = _public_safe(value)
    if _englishish(text):
        fallback = {
            "next": "очікує наступного безпечного кроку.",
            "blocker": "потребує перевірки оператором.",
            "current": "працює у підтвердженому режимі лише читання.",
            "finished": "підтверджено в останньому зрізі.",
        }
        return fallback.get(focus, "стан видимий у канонічному зрізі.")
    return text


def _englishish(value: str) -> bool:
    ascii_letters = sum(1 for char in value if char.isascii() and char.isalpha())
    letters = sum(1 for char in value if char.isalpha())
    return letters > 0 and ascii_letters / letters > 0.45


def build_operator_live_state(
    sources: Sequence[Any],
    *,
    refreshed_at: datetime | None = None,
    freshness: str = "current",
) -> OperatorLiveState:
    return operator_live_state_from_overview(
        build_operator_overview(sources),
        refreshed_at=refreshed_at,
        freshness=freshness,
    )
