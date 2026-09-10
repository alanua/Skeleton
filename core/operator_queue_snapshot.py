from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Final


OPERATOR_QUEUE_SNAPSHOT_SCHEMA: Final = "skeleton.operator_queue_snapshot.v1"

LABEL_READY: Final = "runner:ready"
LABEL_RUN_NOW: Final = "queue:RUN_NOW"
LABEL_RUNNING: Final = "runner:running"
LABEL_DONE: Final = "runner:done"
LABEL_BLOCKED: Final = "runner:blocked"
LABEL_WAITING_DEPENDENCY: Final = "runner:waiting-dependency"
NEEDS_OPERATOR_LABELS: Final = frozenset(
    {
        "runner:needs-operator",
        "needs-operator",
        "NEEDS_OPERATOR",
        "status:NEEDS_OPERATOR",
    }
)
PRIVATE_LABELS: Final = frozenset(
    {"privacy:private", "privacy:PRIVATE", "private", "payload:private"}
)

STATUS_ORDER: Final = {
    "running": 0,
    "blocked": 1,
    "ready": 2,
    "done": 3,
    "inactive": 4,
}
STATUS_LABEL_UK: Final = {
    "ready": "Готово",
    "running": "В роботі",
    "blocked": "Заблоковано",
    "done": "Завершено",
    "inactive": "Поза активною чергою",
}

_SECRET_LIKE_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|bearer\s+[a-z0-9._~+/-]+)"
)
_UNSAFE_OUTPUT_KEYS = frozenset(
    {
        "body",
        "payload",
        "task",
        "task_body",
        "private_payload",
        "secret",
        "token",
        "password",
        "authorization",
    }
)


@dataclass(frozen=True)
class OperatorQueueItem:
    issue_number: int
    issue_ref: str
    status: str
    status_label_uk: str
    operator_hint_uk: str
    url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "issue_number": self.issue_number,
            "issue_ref": self.issue_ref,
            "status": self.status,
            "status_label_uk": self.status_label_uk,
            "operator_hint_uk": self.operator_hint_uk,
        }
        if self.url is not None:
            value["url"] = self.url
        return value


@dataclass(frozen=True)
class OperatorQueueSnapshot:
    schema: str
    public_safe: bool
    snapshot_id: str
    counts: Mapping[str, int]
    status_uk: str
    summary_uk: str
    operator_hint_uk: str
    items: tuple[OperatorQueueItem, ...]
    redacted_private_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "public_safe": self.public_safe,
            "snapshot_id": self.snapshot_id,
            "counts": dict(self.counts),
            "status_uk": self.status_uk,
            "summary_uk": self.summary_uk,
            "operator_hint_uk": self.operator_hint_uk,
            "items": [item.as_dict() for item in self.items],
            "redacted_private_count": self.redacted_private_count,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.as_dict(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def build_operator_queue_snapshot(
    issues: Sequence[Mapping[str, Any]],
) -> OperatorQueueSnapshot:
    """Build a deterministic public-safe Dashboard read model from issue records.

    The caller owns fetching issue records. This function never calls GitHub and
    intentionally ignores issue body, title, and task payload fields.
    """
    items: list[OperatorQueueItem] = []
    redacted_private_count = 0
    for issue in issues:
        if not isinstance(issue, Mapping) or _is_pull_request(issue):
            continue
        labels = _label_names(issue.get("labels"))
        if labels & PRIVATE_LABELS or _privacy_boundary_is_private(issue):
            redacted_private_count += 1
            continue
        number = _issue_number(issue)
        if number is None:
            continue
        status = _issue_status(issue, labels)
        items.append(
            OperatorQueueItem(
                issue_number=number,
                issue_ref=f"#{number}",
                status=status,
                status_label_uk=STATUS_LABEL_UK[status],
                operator_hint_uk=_item_hint_uk(status),
                url=_safe_url(issue.get("url") or issue.get("html_url")),
            )
        )

    items.sort(key=lambda item: (STATUS_ORDER[item.status], item.issue_number))
    counts = {
        "active": sum(1 for item in items if item.status in {"ready", "running", "blocked"}),
        "ready": sum(1 for item in items if item.status == "ready"),
        "running": sum(1 for item in items if item.status == "running"),
        "blocked": sum(1 for item in items if item.status == "blocked"),
        "done": sum(1 for item in items if item.status == "done"),
    }
    status_uk, summary_uk, operator_hint_uk = _snapshot_status_fields(counts)
    sanitized = {
        "counts": counts,
        "items": [item.as_dict() for item in items],
        "redacted_private_count": redacted_private_count,
        "schema": OPERATOR_QUEUE_SNAPSHOT_SCHEMA,
    }
    snapshot_id = "operator-queue-" + hashlib.sha256(
        json.dumps(
            sanitized,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    snapshot = OperatorQueueSnapshot(
        schema=OPERATOR_QUEUE_SNAPSHOT_SCHEMA,
        public_safe=True,
        snapshot_id=snapshot_id,
        counts=counts,
        status_uk=status_uk,
        summary_uk=summary_uk,
        operator_hint_uk=operator_hint_uk,
        items=tuple(items),
        redacted_private_count=redacted_private_count,
    )
    _assert_public_safe(snapshot.as_dict())
    return snapshot


def _issue_number(issue: Mapping[str, Any]) -> int | None:
    number = issue.get("number")
    if isinstance(number, int) and not isinstance(number, bool) and number > 0:
        return number
    return None


def _label_names(labels: object) -> frozenset[str]:
    if not isinstance(labels, list):
        return frozenset()
    names: set[str] = set()
    for label in labels:
        if isinstance(label, str):
            names.add(label)
        elif isinstance(label, Mapping) and isinstance(label.get("name"), str):
            names.add(str(label["name"]))
    return frozenset(names)


def _is_pull_request(issue: Mapping[str, Any]) -> bool:
    if issue.get("pull_request") is not None:
        return True
    url = str(issue.get("url") or issue.get("html_url") or "")
    return "/pull/" in url or "/pulls/" in url


def _privacy_boundary_is_private(issue: Mapping[str, Any]) -> bool:
    value = issue.get("privacy_boundary")
    if isinstance(value, str) and value.strip().upper().startswith("PRIVATE"):
        return True
    return False


def _issue_status(issue: Mapping[str, Any], labels: frozenset[str]) -> str:
    if LABEL_BLOCKED in labels or LABEL_WAITING_DEPENDENCY in labels or labels & NEEDS_OPERATOR_LABELS:
        return "blocked"
    if LABEL_DONE in labels:
        return "done"
    if LABEL_RUNNING in labels:
        return "running"
    if LABEL_READY in labels or LABEL_RUN_NOW in labels:
        return "ready"
    return "inactive"


def _safe_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith("https://github.com/") and not _SECRET_LIKE_RE.search(value):
        return value
    return None


def _item_hint_uk(status: str) -> str:
    if status == "running":
        return "Не змінювати репозиторій до завершення задачі."
    if status == "blocked":
        return "Потрібна перевірка оператора."
    if status == "ready":
        return "Готово до підбору Runner."
    if status == "done":
        return "Додаткової дії не потрібно."
    return "Не входить до активної черги."


def _snapshot_status_fields(counts: Mapping[str, int]) -> tuple[str, str, str]:
    if counts["running"] > 0:
        return (
            "В роботі",
            f"Активних задач: {counts['active']}; виконується: {counts['running']}.",
            "Не змінювати репозиторій, доки Runner працює.",
        )
    if counts["blocked"] > 0:
        return (
            "Потрібна увага",
            f"Заблоковано: {counts['blocked']}; готово: {counts['ready']}.",
            "Перевірити заблоковані задачі перед новим запуском.",
        )
    if counts["ready"] > 0:
        return (
            "Готово до запуску",
            f"Готово: {counts['ready']}; виконується: 0.",
            "Runner може взяти наступну задачу.",
        )
    return (
        "Черга вільна",
        f"Активних задач: 0; завершено в зрізі: {counts['done']}.",
        "Немає дій для активної черги.",
    )


def _assert_public_safe(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).lower()
            if key_text in _UNSAFE_OUTPUT_KEYS:
                raise ValueError(f"operator queue snapshot contains unsafe key: {key}")
            _assert_public_safe(child)
        return
    if isinstance(value, list | tuple):
        for child in value:
            _assert_public_safe(child)
        return
    if isinstance(value, str) and _SECRET_LIKE_RE.search(value):
        raise ValueError("operator queue snapshot contains secret-like text")
