from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from html import escape
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

import yaml


UNCERTAIN_LABEL = "Потребує перевірки"
NO_RELIABLE_PROGRESS = "Немає надійної оцінки"


class OperatorOverviewStatus(StrEnum):
    LIVE = "LIVE"
    PARTIAL = "PARTIAL"
    BUILDING = "BUILDING"
    PLANNED = "PLANNED"
    WAITING = "WAITING"
    NEEDS_OPERATOR = "NEEDS_OPERATOR"
    DEFERRED = "DEFERRED"
    RETIRED = "RETIRED"


GROUP_ORDER: tuple[str, ...] = (
    "core/security",
    "autonomy/self-healing",
    "memory/knowledge",
    "mail/documents/calendar",
    "Home/Home Edge",
    "AI executors",
    "domains/projects",
    "interfaces/control",
)


STATUS_UKRAINIAN: dict[OperatorOverviewStatus, str] = {
    OperatorOverviewStatus.LIVE: "Працює",
    OperatorOverviewStatus.PARTIAL: "Працює частково",
    OperatorOverviewStatus.BUILDING: "Будується",
    OperatorOverviewStatus.PLANNED: "Заплановано",
    OperatorOverviewStatus.WAITING: "Очікує",
    OperatorOverviewStatus.NEEDS_OPERATOR: "Потрібен оператор",
    OperatorOverviewStatus.DEFERRED: "Відкладено",
    OperatorOverviewStatus.RETIRED: "Завершено або знято",
}


_RAW_REF_RE = re.compile(
    r"(?i)(?:\b(?:issue|pr)\s*#?\d+\b|#\d+\b|\b[0-9a-f]{7,40}\b|\bPR\s+\d+\b)"
)


@dataclass(frozen=True)
class AcceptanceGate:
    gate_id: str
    passed: bool
    label_uk: str


@dataclass(frozen=True)
class OperatorOverviewSource:
    source_id: str
    source_path: str
    status: str
    summary: str
    group: str
    last_verified: date | None = None
    current_focus: str = ""
    next_milestone: str = ""
    blocker_plain: str = ""
    acceptance_gates: tuple[AcceptanceGate, ...] = ()
    raw_refs: tuple[str, ...] = ()
    superseded_by: str | None = None
    supersession_reason: str | None = None
    retained: bool = True


@dataclass(frozen=True)
class ProgressView:
    label: str
    percent: int | None
    explanation: str


@dataclass(frozen=True)
class OperatorOverviewItem:
    item_id: str
    group: str
    human_name: str
    purpose: str
    status: OperatorOverviewStatus
    status_label: str
    current_focus: str
    next_milestone: str
    blocker_plain: str
    progress: ProgressView
    source_paths: tuple[str, ...]
    drilldown_refs: tuple[str, ...] = ()
    needs_verification: bool = False
    retained: bool = True
    supersession_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "group": self.group,
            "human_name": self.human_name,
            "purpose": self.purpose,
            "status": self.status.value,
            "status_label": self.status_label,
            "current_focus": self.current_focus,
            "next_milestone": self.next_milestone,
            "blocker_plain": self.blocker_plain,
            "progress": {
                "label": self.progress.label,
                "percent": self.progress.percent,
                "explanation": self.progress.explanation,
            },
            "source_paths": list(self.source_paths),
            "drilldown_refs": list(self.drilldown_refs),
            "needs_verification": self.needs_verification,
            "retained": self.retained,
            "supersession_reason": self.supersession_reason,
        }


@dataclass(frozen=True)
class OperatorOverviewGroup:
    group_id: str
    human_name: str
    items: tuple[OperatorOverviewItem, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "human_name": self.human_name,
            "items": [item.as_dict() for item in self.items],
        }


@dataclass(frozen=True)
class OperatorOverview:
    schema: str
    generated_from: tuple[str, ...]
    groups: tuple[OperatorOverviewGroup, ...]
    recent_changes: tuple[str, ...]
    next_milestone: str
    needs_operator: tuple[OperatorOverviewItem, ...]
    retained_portfolio: tuple[OperatorOverviewItem, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "generated_from": list(self.generated_from),
            "groups": [group.as_dict() for group in self.groups],
            "recent_changes": list(self.recent_changes),
            "next_milestone": self.next_milestone,
            "needs_operator": [item.as_dict() for item in self.needs_operator],
            "retained_portfolio": [item.as_dict() for item in self.retained_portfolio],
        }


def load_operator_overview(repo_root: str | Path = ".", *, today: date | None = None) -> OperatorOverview:
    root = Path(repo_root)
    sources: list[OperatorOverviewSource] = []
    sources.extend(_capability_sources(root))
    sources.extend(_project_sources(root))
    sources.extend(_skeleton_state_sources(root))
    sources.extend(_migration_sources(root))
    sources.extend(_build_plan_sources(root))
    return build_operator_overview(sources, today=today)


def build_operator_overview(
    sources: Sequence[OperatorOverviewSource],
    *,
    today: date | None = None,
) -> OperatorOverview:
    current_day = today or date.today()
    by_id: dict[str, list[OperatorOverviewSource]] = {}
    for source in sources:
        by_id.setdefault(source.source_id, []).append(source)

    items = [_source_group_to_item(source_id, source_group, current_day) for source_id, source_group in by_id.items()]
    items.sort(key=lambda item: (GROUP_ORDER.index(item.group), item.status.value, item.human_name))
    grouped = tuple(
        OperatorOverviewGroup(
            group_id=group_id,
            human_name=group_id,
            items=tuple(item for item in items if item.group == group_id),
        )
        for group_id in GROUP_ORDER
    )
    needs_operator = tuple(item for item in items if item.status == OperatorOverviewStatus.NEEDS_OPERATOR)
    retained = tuple(item for item in items if item.retained)
    _validate_retained_portfolio(retained)
    next_milestone = _first_text((item.next_milestone for item in items), "Наступний крок ще не визначено.")
    return OperatorOverview(
        schema="skeleton.operator_overview.read_model.v0",
        generated_from=tuple(sorted({path for item in items for path in item.source_paths})),
        groups=grouped,
        recent_changes=_recent_changes(items),
        next_milestone=next_milestone,
        needs_operator=needs_operator,
        retained_portfolio=retained,
    )


def render_operator_overview_mobile_html(overview: OperatorOverview) -> str:
    """Render the first phone-width read-only surface without exposing raw refs as primary labels."""
    group_sections = "\n".join(_render_group(group) for group in overview.groups if group.items)
    needs = "".join(f"<li>{escape(item.human_name)}: {escape(item.blocker_plain)}</li>" for item in overview.needs_operator)
    changes = "".join(f"<li>{escape(change)}</li>" for change in overview.recent_changes)
    portfolio_count = len(overview.retained_portfolio)
    return f"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Огляд оператора</title>
<style>
:root {{ color-scheme: light; --ink:#16181d; --muted:#5c6470; --line:#d7dce3; --live:#177245; --warn:#9a5b00; --wait:#7357a4; --bg:#f7f8fa; }}
body {{ margin:0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:var(--bg); color:var(--ink); }}
main {{ max-width: 760px; margin: 0 auto; padding: 16px; }}
header {{ padding: 10px 0 14px; border-bottom: 1px solid var(--line); }}
h1 {{ font-size: 1.65rem; line-height: 1.15; margin: 0 0 8px; letter-spacing: 0; }}
h2 {{ font-size: 1.05rem; margin: 22px 0 10px; letter-spacing: 0; }}
h3 {{ font-size: 1rem; margin: 0; letter-spacing: 0; }}
p {{ margin: 6px 0; color: var(--muted); }}
.summary {{ display:grid; grid-template-columns: 1fr 1fr; gap:8px; margin-top: 12px; }}
.tile, article {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:12px; }}
.tile strong {{ display:block; font-size:1.2rem; }}
.items {{ display:grid; gap:10px; }}
.meta {{ display:flex; flex-wrap:wrap; gap:6px; margin:8px 0; }}
.pill {{ border:1px solid var(--line); border-radius:999px; padding:3px 8px; font-size:.82rem; color:var(--muted); }}
.LIVE {{ color:var(--live); border-color:#b9dfcc; }}
.NEEDS_OPERATOR, .WAITING {{ color:var(--warn); border-color:#efd5a8; }}
.BUILDING, .PLANNED, .PARTIAL {{ color:var(--wait); border-color:#d8c9ee; }}
details {{ margin-top:8px; }}
summary {{ color:var(--muted); cursor:pointer; }}
ul {{ padding-left: 1.1rem; }}
@media (max-width: 480px) {{ main {{ padding: 12px; }} .summary {{ grid-template-columns: 1fr; }} h1 {{ font-size:1.45rem; }} }}
</style>
</head>
<body>
<main>
<header>
<h1>Огляд оператора</h1>
<p>Публічно безпечний, read-only зріз із наявних реєстрів і контрольних метаданих.</p>
<div class="summary">
<div class="tile"><strong>{portfolio_count}</strong><span>утриманих можливостей та ідей</span></div>
<div class="tile"><strong>{len(overview.needs_operator)}</strong><span>пунктів потребують оператора</span></div>
</div>
</header>
<section><h2>Що потребує оператора</h2><ul>{needs or "<li>Немає явних пунктів.</li>"}</ul></section>
<section><h2>Наступний рубіж</h2><p>{escape(overview.next_milestone)}</p></section>
<section><h2>Останні зміни</h2><ul>{changes}</ul></section>
{group_sections}
</main>
</body>
</html>"""


def _render_group(group: OperatorOverviewGroup) -> str:
    cards = "\n".join(_render_item(item) for item in group.items)
    return f'<section><h2>{escape(group.human_name)}</h2><div class="items">{cards}</div></section>'


def _render_item(item: OperatorOverviewItem) -> str:
    refs = "".join(f"<li>{escape(ref)}</li>" for ref in item.drilldown_refs)
    sources = "".join(f"<li>{escape(path)}</li>" for path in item.source_paths)
    return f"""<article>
<h3>{escape(item.human_name)}</h3>
<div class="meta"><span class="pill {item.status.value}">{escape(item.status_label)}</span><span class="pill">{escape(item.progress.label)}</span></div>
<p>{escape(item.purpose)}</p>
<p><strong>Фокус:</strong> {escape(item.current_focus)}</p>
<p><strong>Далі:</strong> {escape(item.next_milestone)}</p>
<p><strong>Блокер:</strong> {escape(item.blocker_plain)}</p>
<details><summary>Технічні джерела</summary><ul>{sources}{refs}</ul></details>
</article>"""


def _source_group_to_item(source_id: str, sources: Sequence[OperatorOverviewSource], today: date) -> OperatorOverviewItem:
    primary = sources[0]
    statuses = {_normalize_status(source.status) for source in sources}
    contradictory = len(statuses) > 1
    stale = any(_is_stale(source.last_verified, today) for source in sources)
    needs_verification = contradictory or stale
    status = OperatorOverviewStatus.NEEDS_OPERATOR if needs_verification else _normalize_status(primary.status)
    status_label = UNCERTAIN_LABEL if needs_verification else STATUS_UKRAINIAN[status]
    gates = tuple(gate for source in sources for gate in source.acceptance_gates)
    progress = _progress_from_gates(gates)
    raw_refs = tuple(dict.fromkeys(ref for source in sources for ref in source.raw_refs))
    return OperatorOverviewItem(
        item_id=source_id,
        group=primary.group if primary.group in GROUP_ORDER else "interfaces/control",
        human_name=_human_name(source_id),
        purpose=_primary_safe(_first_text((source.summary for source in sources), "Призначення не описано.")),
        status=status,
        status_label=status_label,
        current_focus=_primary_safe(_first_text((source.current_focus for source in sources), "Підтримувати поточний стан.")),
        next_milestone=_primary_safe(_first_text((source.next_milestone for source in sources), "Немає явного наступного рубежу.")),
        blocker_plain=UNCERTAIN_LABEL if needs_verification else _primary_safe(_first_text((source.blocker_plain for source in sources), "Явного блокера немає.")),
        progress=progress,
        source_paths=tuple(sorted({source.source_path for source in sources})),
        drilldown_refs=raw_refs,
        needs_verification=needs_verification,
        retained=any(source.retained for source in sources),
        supersession_reason=_first_text((source.supersession_reason for source in sources), "") or None,
    )


def _capability_sources(root: Path) -> list[OperatorOverviewSource]:
    data = _load_yaml(root / "CAPABILITY_REGISTRY.yaml")
    sources = []
    for capability_id, raw in (data.get("capabilities") or {}).items():
        if not isinstance(raw, Mapping):
            continue
        status = str(raw.get("status") or "planned")
        gates = ()
        if "tested" in raw:
            gates = (AcceptanceGate("tested", bool(raw.get("tested")), "Тест проходить"),)
        refs = tuple(str(ref) for ref in _listify(raw.get("requires"))) + tuple(_raw_refs(str(raw.get("description") or "")))
        sources.append(
            OperatorOverviewSource(
                source_id=f"capability:{capability_id}",
                source_path="CAPABILITY_REGISTRY.yaml",
                status=status,
                summary=str(raw.get("description") or capability_id),
                group=_group_for_capability(capability_id, raw),
                current_focus="Утримувати перевірений read-only стан." if bool(raw.get("tested")) else "Довести до перевіреного стану.",
                next_milestone=_primary_safe(str(raw.get("entry") or raw.get("module") or "Зберегти в реєстрі.")),
                acceptance_gates=gates,
                raw_refs=refs,
            )
        )
    return sources


def _project_sources(root: Path) -> list[OperatorOverviewSource]:
    data = _load_yaml(root / "PROJECT_INDEX.yaml")
    sources = []
    for project_id, raw in (data.get("projects") or {}).items():
        if not isinstance(raw, Mapping):
            continue
        sources.append(
            OperatorOverviewSource(
                source_id=f"project:{project_id}",
                source_path="PROJECT_INDEX.yaml",
                status=str(data.get("status") or "planned"),
                summary=str(raw.get("means") or project_id),
                group="domains/projects",
                current_focus="Зберігати маршрут і межу проєкту видимими.",
                next_milestone=str(raw.get("entrypoint") or "Оновити проєктний маніфест після перевірки."),
                raw_refs=(str(raw.get("repo") or ""), str(raw.get("entrypoint") or "")),
            )
        )
    return sources


def _skeleton_state_sources(root: Path) -> list[OperatorOverviewSource]:
    path = root / "projects/skeleton/STATE.yaml"
    data = _load_yaml(path)
    sources = []
    for index, line in enumerate(_listify(data.get("summary"))):
        sources.append(
            OperatorOverviewSource(
                source_id=f"skeleton_state:{index}",
                source_path="projects/skeleton/STATE.yaml",
                status=str(data.get("status") or "waiting"),
                summary=str(line),
                group=_group_for_text(str(line)),
                last_verified=_parse_date(data.get("last_verified")),
                current_focus=str(data.get("state_role") or "handoff_not_canon_truth"),
                next_milestone=_first_text((str(action) for action in _listify(data.get("next_actions"))), ""),
                raw_refs=tuple(_raw_refs(str(line))),
            )
        )
    return sources


def _migration_sources(root: Path) -> list[OperatorOverviewSource]:
    data = _load_yaml(root / "projects/skeleton/MIGRATION_STATUS.yaml")
    sources = []
    for raw in _listify(data.get("status_entries")):
        if not isinstance(raw, Mapping):
            continue
        sources.append(
            OperatorOverviewSource(
                source_id=f"migration:{raw.get('id')}",
                source_path="projects/skeleton/MIGRATION_STATUS.yaml",
                status=str(raw.get("state") or raw.get("category") or "planned"),
                summary=str(raw.get("summary") or raw.get("id")),
                group=_group_for_text(" ".join([str(raw.get("id") or ""), str(raw.get("summary") or "")])),
                current_focus=str(raw.get("category") or "migration"),
                next_milestone=str(raw.get("next_action") or ""),
                blocker_plain="Потрібен перегляд." if str(raw.get("category")) == "pending_review" else "",
                raw_refs=tuple(str(ref) for ref in _listify(raw.get("source_of_truth"))),
                retained=str(raw.get("category")) != "rejected",
                supersession_reason="Відхилено в міграційному реєстрі." if str(raw.get("category")) == "rejected" else None,
            )
        )
    return sources


def _build_plan_sources(root: Path) -> list[OperatorOverviewSource]:
    data = _load_yaml(root / "docs/SKELETON_BUILD_PLAN.yaml")
    sources = []
    superseded_by = data.get("superseded_for_target_architecture_by")
    for raw in _listify(data.get("phases")):
        if not isinstance(raw, Mapping):
            continue
        phase_id = str(raw.get("id"))
        name = str(raw.get("name") or phase_id)
        sources.append(
            OperatorOverviewSource(
                source_id=f"build_phase:{phase_id}",
                source_path="docs/SKELETON_BUILD_PLAN.yaml",
                status="planned" if phase_id not in {"0", "1", "2"} else "partial",
                summary=name.replace("_", " "),
                group=_group_for_text(name),
                current_focus="Утримана ідея з плану розвитку.",
                next_milestone=_first_text((str(item) for item in _listify(data.get("next_milestones"))), ""),
                raw_refs=tuple(str(path) for path in _listify((data.get("documents") or {}).values())),
                superseded_by=str(superseded_by) if superseded_by else None,
                supersession_reason=f"Цільова архітектура уточнена в {superseded_by}." if superseded_by else None,
            )
        )
    for milestone in _listify(data.get("next_milestones")):
        sources.append(
            OperatorOverviewSource(
                source_id=f"milestone:{milestone}",
                source_path="docs/SKELETON_BUILD_PLAN.yaml",
                status="planned",
                summary=str(milestone).replace("_", " "),
                group=_group_for_text(str(milestone)),
                current_focus="Очікує виконання за планом.",
                next_milestone=str(milestone).replace("_", " "),
            )
        )
    return sources


def _progress_from_gates(gates: Sequence[AcceptanceGate]) -> ProgressView:
    if not gates:
        return ProgressView(NO_RELIABLE_PROGRESS, None, "Немає явних acceptance gates.")
    passed = sum(1 for gate in gates if gate.passed)
    percent = round((passed / len(gates)) * 100)
    return ProgressView(f"{percent}%", percent, f"{passed}/{len(gates)} явних gates виконано.")


def _normalize_status(raw_status: str) -> OperatorOverviewStatus:
    raw = raw_status.lower()
    if raw in {"available", "active", "active_public_safe", "migrated", "done", "ok", "live"}:
        return OperatorOverviewStatus.LIVE
    if "stage1" in raw or "stage_1" in raw or "partial" in raw or "dry_run" in raw:
        return OperatorOverviewStatus.PARTIAL
    if "draft" in raw or "build" in raw or "candidate" in raw:
        return OperatorOverviewStatus.BUILDING
    if "review" in raw or "pending" in raw or "waiting" in raw:
        return OperatorOverviewStatus.WAITING
    if "operator" in raw or "blocked" in raw:
        return OperatorOverviewStatus.NEEDS_OPERATOR
    if "private" in raw or "future" in raw or "defer" in raw:
        return OperatorOverviewStatus.DEFERRED
    if "reject" in raw or "retired" in raw:
        return OperatorOverviewStatus.RETIRED
    if "plan" in raw:
        return OperatorOverviewStatus.PLANNED
    return OperatorOverviewStatus.PLANNED


def _group_for_capability(capability_id: str, raw: Mapping[str, Any]) -> str:
    text = " ".join([capability_id, str(raw.get("module") or ""), str(raw.get("description") or "")]).lower()
    return _group_for_text(text)


def _group_for_text(text: str) -> str:
    value = text.lower()
    if any(token in value for token in ("home_edge", "home edge", "home/")):
        return "Home/Home Edge"
    if any(token in value for token in ("memory", "cognee", "graph", "mempalace", "knowledge", "notebook")):
        return "memory/knowledge"
    if any(token in value for token in ("runner", "scheduler", "loop", "recovery", "self-heal", "maintenance")):
        return "autonomy/self-healing"
    if any(token in value for token in ("codex", "gemini", "hermes", "provider", "executor", "adapter")):
        return "AI executors"
    if any(token in value for token in ("mail", "document", "calendar", "contact")):
        return "mail/documents/calendar"
    if any(token in value for token in ("project", "aufmass", "dios", "travel", "lavalamp", "bauclock", "homelab", "gewerbe", "van", "jeeves", "domain")):
        return "domains/projects"
    if any(token in value for token in ("telegram", "page", "interface", "dashboard", "command", "mode", "operator")):
        return "interfaces/control"
    return "core/security"


def _human_name(source_id: str) -> str:
    tail = source_id.split(":", 1)[-1]
    replacements = {
        "boot_manifest": "Маніфест запуску",
        "command_modes": "Команди і режими",
        "memory_routing": "Маршрути памʼяті",
        "project_manifests": "Маніфести проєктів",
        "write_gate": "Гейт запису",
        "github_task_queue": "Черга Runner",
        "runner_worktree_execution": "Виконання Runner у worktree",
        "operator_overview": "Огляд оператора",
    }
    if tail in replacements:
        return replacements[tail]
    return tail.replace("_", " ").replace("-", " ").strip().capitalize()


def _primary_safe(value: str) -> str:
    cleaned = _RAW_REF_RE.sub("технічний ref", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "Немає явного опису."


def _raw_refs(value: str) -> list[str]:
    return [match.group(0) for match in _RAW_REF_RE.finditer(value)]


def _recent_changes(items: Sequence[OperatorOverviewItem]) -> tuple[str, ...]:
    selected = [item for item in items if item.status in {OperatorOverviewStatus.LIVE, OperatorOverviewStatus.PARTIAL}]
    return tuple(f"{item.human_name}: {item.status_label}" for item in selected[:6]) or ("Немає надійно визначених змін.",)


def _is_stale(last_verified: date | None, today: date) -> bool:
    return bool(last_verified and (today - last_verified).days > 45)


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _listify(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, Mapping):
        return list(value.values())
    return [value]


def _first_text(values: Iterable[str], fallback: str) -> str:
    for value in values:
        text = str(value).strip()
        if text:
            return text
    return fallback


def _validate_retained_portfolio(items: Sequence[OperatorOverviewItem]) -> None:
    seen: dict[str, OperatorOverviewItem] = {}
    for item in items:
        previous = seen.get(item.item_id)
        if previous and not (item.supersession_reason or previous.supersession_reason):
            raise ValueError(f"retained_portfolio_duplicate:{item.item_id}")
        seen[item.item_id] = item
