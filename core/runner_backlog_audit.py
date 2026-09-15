from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

import yaml


AUDIT_SCHEMA = "skeleton.runner_backlog_audit.v1"

CATEGORY_ACTIVE = "active"
CATEGORY_SUPERSEDED = "superseded"
CATEGORY_DUPLICATE_CANDIDATE = "duplicate-candidate"
CATEGORY_STALE_BASE = "stale-base"
CATEGORY_NEEDS_OPERATOR = "needs-operator"

AUDIT_CATEGORIES = (
    CATEGORY_ACTIVE,
    CATEGORY_SUPERSEDED,
    CATEGORY_DUPLICATE_CANDIDATE,
    CATEGORY_STALE_BASE,
    CATEGORY_NEEDS_OPERATOR,
)

LABEL_READY = "runner:ready"
LABEL_RUN_NOW = "queue:RUN_NOW"
LABEL_RUNNING = "runner:running"
LABEL_DONE = "runner:done"
LABEL_BLOCKED = "runner:blocked"
LABEL_AGENT_TASK = "agent:task"
LABEL_WAITING_DEPENDENCY = "runner:waiting-dependency"

ACTIVE_EXECUTION_LABELS = frozenset((LABEL_READY, LABEL_RUN_NOW, LABEL_RUNNING))
TERMINAL_RUNNER_LABELS = frozenset((LABEL_DONE, LABEL_BLOCKED))
BACKLOG_LABELS = frozenset((LABEL_AGENT_TASK, "runner:backlog"))

NEEDS_OPERATOR_LABELS = frozenset(
    (
        "runner:needs-operator",
        "needs-operator",
        "NEEDS_OPERATOR",
        "status:NEEDS_OPERATOR",
    )
)
PRIVATE_LABELS = frozenset(
    ("privacy:private", "privacy:PRIVATE", "private", "payload:private")
)
PUBLIC_SAFE_LABELS = frozenset(
    ("privacy:public-safe", "privacy:PUBLIC_SAFE", "public-safe")
)

_HEAD_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_FIELD_RE_TEMPLATE = r"^[^\S\r\n]*{field}:[^\S\r\n]*(?P<value>\S(?:[^\r\n]*\S)?)[^\S\r\n]*$"
_INTENT_WORD_RE = re.compile(r"[^a-z0-9]+")
_TASK_FENCE_OPEN_RE = re.compile(r"^\s*```\s*task\s*$")
_FENCE_CLOSE_RE = re.compile(r"^\s*```\s*$")


@dataclass(frozen=True)
class IssueAuditEntry:
    number: int
    category: str
    reasons: tuple[str, ...]
    title: str
    labels: tuple[str, ...]
    url: str | None = None
    intent_key: str | None = None
    base_branch: str | None = None
    expected_base_sha: str | None = None
    pull_request: int | None = None
    expected_head_sha: str | None = None
    matching_issues: tuple[int, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "number": self.number,
            "category": self.category,
            "reasons": list(self.reasons),
            "title": self.title,
            "labels": list(self.labels),
        }
        if self.url:
            result["url"] = self.url
        if self.intent_key:
            result["intent_key"] = self.intent_key
        if self.base_branch:
            result["base_branch"] = self.base_branch
        if self.expected_base_sha:
            result["expected_base_sha"] = self.expected_base_sha
        if self.pull_request:
            result["pull_request"] = self.pull_request
        if self.expected_head_sha:
            result["expected_head_sha"] = self.expected_head_sha
        if self.matching_issues:
            result["matching_issues"] = list(self.matching_issues)
        return result


def audit_runner_backlog(
    issues: list[Mapping[str, Any]],
    *,
    base_heads: Mapping[str, str] | None = None,
    pr_heads: Mapping[int, str] | None = None,
    repo: str = "alanua/Skeleton",
) -> dict[str, Any]:
    """Classify public Runner issue backlog without mutating GitHub state."""
    base_heads = {key: value.lower() for key, value in (base_heads or {}).items()}
    pr_heads = {key: value.lower() for key, value in (pr_heads or {}).items()}
    issue_items = [_issue_snapshot(issue) for issue in issues if _is_runner_issue(issue)]
    duplicate_sets = _duplicate_sets(issue_items)

    entries = [
        _classify_issue(
            issue,
            duplicate_sets=duplicate_sets,
            base_heads=base_heads,
            pr_heads=pr_heads,
        )
        for issue in sorted(issue_items, key=lambda item: item["number"])
    ]
    counts = {category: 0 for category in AUDIT_CATEGORIES}
    items_by_category = {category: [] for category in AUDIT_CATEGORIES}
    for entry in entries:
        counts[entry.category] += 1
        items_by_category[entry.category].append(entry.to_mapping())
    return {
        "schema": AUDIT_SCHEMA,
        "repo": repo,
        "counts": counts,
        "items": items_by_category,
    }


def compact_report(audit: Mapping[str, Any]) -> str:
    counts = audit.get("counts") if isinstance(audit.get("counts"), Mapping) else {}
    header_counts = " ".join(
        f"{category}={int(counts.get(category, 0))}" for category in AUDIT_CATEGORIES
    )
    lines = [
        f"RUNNER_BACKLOG_AUDIT {audit.get('repo', 'unknown')} {header_counts}",
    ]
    items = audit.get("items") if isinstance(audit.get("items"), Mapping) else {}
    for category in AUDIT_CATEGORIES:
        category_items = items.get(category, [])
        if not isinstance(category_items, list):
            continue
        for item in category_items:
            if not isinstance(item, Mapping):
                continue
            reasons = ",".join(str(reason) for reason in item.get("reasons", []))
            suffix_parts = []
            if item.get("intent_key"):
                suffix_parts.append(f"intent={item['intent_key']}")
            if item.get("expected_base_sha"):
                suffix_parts.append(
                    f"base={item.get('base_branch', 'main')}@{item['expected_base_sha']}"
                )
            if item.get("pull_request") and item.get("expected_head_sha"):
                suffix_parts.append(
                    f"pr=#{item.get('pull_request')}@{item['expected_head_sha']}"
                )
            if item.get("matching_issues"):
                suffix_parts.append(
                    "matches="
                    + ",".join(f"#{number}" for number in item["matching_issues"])
                )
            suffix = " " + " ".join(suffix_parts) if suffix_parts else ""
            lines.append(f"{category} #{item.get('number')} {reasons}{suffix}".rstrip())
    return "\n".join(lines)


def _classify_issue(
    issue: Mapping[str, Any],
    *,
    duplicate_sets: Mapping[int, tuple[int, ...]],
    base_heads: Mapping[str, str],
    pr_heads: Mapping[int, str],
) -> IssueAuditEntry:
    labels = issue["labels"]
    reasons: list[str] = []

    category = CATEGORY_ACTIVE
    privacy_reason = _privacy_block_reason(issue)
    if labels & NEEDS_OPERATOR_LABELS:
        category = CATEGORY_NEEDS_OPERATOR
        reasons.append("operator_label")
    elif privacy_reason is not None:
        category = CATEGORY_NEEDS_OPERATOR
        reasons.append(privacy_reason)
    elif not issue["has_task_block"]:
        category = CATEGORY_NEEDS_OPERATOR
        reasons.append("missing_task_block")
    elif labels & TERMINAL_RUNNER_LABELS:
        category = CATEGORY_SUPERSEDED
        reasons.append("terminal_runner_label")
        if labels & ACTIVE_EXECUTION_LABELS:
            reasons.append("terminal_with_active_label")
    elif issue["superseded_by"] is not None:
        category = CATEGORY_SUPERSEDED
        reasons.append("declared_superseded_by")
    elif issue["number"] in duplicate_sets:
        category = CATEGORY_DUPLICATE_CANDIDATE
        reasons.append("duplicate_intent_or_files")
    elif (stale_reason := _stale_base_reason(issue, base_heads, pr_heads)) is not None:
        category = CATEGORY_STALE_BASE
        reasons.append(stale_reason)
    else:
        if LABEL_RUNNING in labels:
            reasons.append("running")
        elif labels & frozenset((LABEL_READY, LABEL_RUN_NOW)):
            reasons.append("queued")
        elif LABEL_WAITING_DEPENDENCY in labels:
            reasons.append("waiting_dependency")
        else:
            reasons.append("eligible_backlog")

    return IssueAuditEntry(
        number=issue["number"],
        category=category,
        reasons=tuple(reasons),
        title=issue["title"],
        labels=tuple(sorted(labels)),
        url=issue["url"],
        intent_key=issue["intent_key"],
        base_branch=issue["base_branch"],
        expected_base_sha=issue["expected_base_sha"],
        pull_request=issue["pull_request"],
        expected_head_sha=issue["expected_head_sha"],
        matching_issues=duplicate_sets.get(issue["number"], ()),
    )


def _issue_snapshot(issue: Mapping[str, Any]) -> dict[str, Any]:
    body = str(issue.get("body") or "")
    task_fields = _task_block_mapping(body)
    metadata = _metadata_before_task(body)
    labels = _label_names(issue.get("labels"))
    number = _issue_number(issue)
    if number is None:
        raise ValueError("Runner issue missing numeric issue number")
    return {
        "number": number,
        "title": str(issue.get("title") or ""),
        "body": body,
        "labels": labels,
        "url": str(issue.get("url") or "") or None,
        "has_task_block": _extract_task_block(body) is not None,
        "intent_key": _intent_key(issue, task_fields, metadata),
        "allowed_files": _field_sequence(
            task_fields, metadata, "Allowed Files", "allowed_files"
        ),
        "base_branch": _field_text(
            task_fields, metadata, "Base Branch", "base_branch"
        )
        or "main",
        "expected_base_sha": _expected_base_sha(task_fields, metadata),
        "expected_head_sha": _field_text(
            task_fields, metadata, "Expected Head SHA", "expected_head_sha"
        ),
        "pull_request": _field_int(task_fields, metadata, "Pull Request", "pull_request"),
        "superseded_by": _field_text(
            task_fields, metadata, "Superseded By", "superseded_by"
        ),
        "privacy_boundary": _field_text(
            task_fields, metadata, "Privacy Boundary", "privacy_boundary"
        ),
        "schema": _field_text(task_fields, metadata, "Schema", "schema"),
    }


def _is_runner_issue(issue: Mapping[str, Any]) -> bool:
    if _issue_number(issue) is None or _is_pull_request_item(issue):
        return False
    if issue.get("closed") is True:
        return False
    state = issue.get("state")
    if state is not None and str(state).lower() != "open":
        return False
    labels = _label_names(issue.get("labels"))
    return bool(
        labels
        & (
            ACTIVE_EXECUTION_LABELS
            | TERMINAL_RUNNER_LABELS
            | BACKLOG_LABELS
            | NEEDS_OPERATOR_LABELS
        )
    )


def _duplicate_sets(issues: list[Mapping[str, Any]]) -> dict[int, tuple[int, ...]]:
    groups: dict[str, list[int]] = {}
    file_owners: dict[str, list[int]] = {}
    for issue in issues:
        labels = issue["labels"]
        if labels & TERMINAL_RUNNER_LABELS or labels & NEEDS_OPERATOR_LABELS:
            continue
        privacy_reason = _privacy_block_reason(issue)
        if privacy_reason is not None:
            continue
        intent_key = issue.get("intent_key")
        if intent_key:
            groups.setdefault(f"intent:{intent_key}", []).append(issue["number"])
        for path in issue.get("allowed_files", ()):
            file_owners.setdefault(f"file:{path}", []).append(issue["number"])

    duplicates: dict[int, set[int]] = {}
    for numbers in (*groups.values(), *file_owners.values()):
        if len(numbers) < 2:
            continue
        ordered = sorted(set(numbers))
        for number in ordered:
            duplicates.setdefault(number, set()).update(
                item for item in ordered if item != number
            )
    return {number: tuple(sorted(matches)) for number, matches in duplicates.items()}


def _stale_base_reason(
    issue: Mapping[str, Any],
    base_heads: Mapping[str, str],
    pr_heads: Mapping[int, str],
) -> str | None:
    expected_base = issue.get("expected_base_sha")
    if isinstance(expected_base, str) and _HEAD_SHA_RE.fullmatch(expected_base):
        actual = base_heads.get(str(issue.get("base_branch") or "main"))
        if actual is not None and actual != expected_base.lower():
            return "base_head_mismatch"
    expected_head = issue.get("expected_head_sha")
    pr_number = issue.get("pull_request")
    if (
        isinstance(pr_number, int)
        and isinstance(expected_head, str)
        and _HEAD_SHA_RE.fullmatch(expected_head)
    ):
        actual_pr_head = pr_heads.get(pr_number)
        if actual_pr_head is not None and actual_pr_head != expected_head.lower():
            return "pr_head_mismatch"
    return None


def _privacy_block_reason(issue: Mapping[str, Any]) -> str | None:
    labels = issue["labels"]
    if labels & PRIVATE_LABELS:
        return "private_label"
    boundary = issue.get("privacy_boundary")
    if isinstance(boundary, str) and boundary.strip():
        normalized = boundary.strip().upper()
        if normalized.startswith("PUBLIC_SAFE"):
            return None
        if normalized.startswith("PRIVATE") or normalized.endswith("_PRIVATE"):
            return "private_privacy_boundary"
        return "unsupported_privacy_boundary"
    schema = issue.get("schema")
    if isinstance(schema, str) and "PRIVATE" in schema.upper():
        return "private_schema"
    if labels & PUBLIC_SAFE_LABELS:
        return None
    return "missing_public_safe_privacy_boundary"


def _expected_base_sha(task_fields: Mapping[str, Any], metadata: str) -> str | None:
    for public_field, typed_key in (
        ("Expected Base SHA", "expected_base_sha"),
        ("Base SHA", "base_sha"),
        ("Expected Main SHA", "expected_main_sha"),
    ):
        value = _field_text(task_fields, metadata, public_field, typed_key)
        if value:
            return value.lower()
    return None


def _intent_key(
    issue: Mapping[str, Any], task_fields: Mapping[str, Any], metadata: str
) -> str:
    for public_field, typed_key in (
        ("Idempotency Key", "idempotency_key"),
        ("Intent Key", "intent_key"),
        ("Operation", "operation"),
        ("Maintenance Task ID", "maintenance_task_id"),
    ):
        value = _field_text(task_fields, metadata, public_field, typed_key)
        if value:
            return value.lower()
    title = str(issue.get("title") or "")
    normalized = _INTENT_WORD_RE.sub(" ", title.lower()).strip()
    return " ".join(normalized.split())


def _field_text(
    task_fields: Mapping[str, Any],
    metadata: str,
    public_field: str,
    typed_key: str,
) -> str | None:
    if typed_key in task_fields and task_fields[typed_key] is not None:
        value = task_fields[typed_key]
        return str(value).strip() or None
    match = re.search(
        _FIELD_RE_TEMPLATE.format(field=re.escape(public_field)),
        metadata,
        re.MULTILINE,
    )
    if match is not None:
        return match.group("value").strip()
    yaml_value = _metadata_yaml_value(metadata, typed_key)
    if yaml_value is None or not str(yaml_value).strip():
        return None
    return str(yaml_value).strip()


def _field_int(
    task_fields: Mapping[str, Any],
    metadata: str,
    public_field: str,
    typed_key: str,
) -> int | None:
    text = _field_text(task_fields, metadata, public_field, typed_key)
    return int(text) if text is not None and text.isdecimal() else None


def _field_sequence(
    task_fields: Mapping[str, Any],
    metadata: str,
    public_field: str,
    typed_key: str,
) -> frozenset[str]:
    value = task_fields.get(typed_key)
    if value is None:
        value = _metadata_yaml_value(metadata, typed_key)
    if value is None:
        multiline = _metadata_list_items(metadata, public_field)
        if multiline is not None:
            value = multiline
    if isinstance(value, str):
        values = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        values = [str(part).strip() for part in value]
    else:
        values = []
    return frozenset(value for value in values if value)


def _metadata_list_items(metadata: str, field: str) -> list[str] | None:
    lines = metadata.splitlines()
    for index, line in enumerate(lines):
        if re.fullmatch(rf"\s*{re.escape(field)}:\s*", line):
            items: list[str] = []
            for item in lines[index + 1 :]:
                if re.fullmatch(r"\s*[A-Za-z][A-Za-z ]+:\s*.*", item):
                    break
                match = re.fullmatch(r"\s*-\s+(?P<value>\S(?:.*\S)?)\s*", item)
                if match is not None:
                    items.append(match.group("value"))
                elif item.strip():
                    return []
            return items
    return None


def _metadata_yaml_value(metadata: str, key: str) -> object:
    for document in _metadata_yaml_documents(metadata):
        if key in document:
            return document[key]
    return None


def _metadata_yaml_documents(metadata: str) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    lines = metadata.splitlines()
    index = 0
    while index < len(lines):
        if re.fullmatch(r"\s*```\s*(?:yaml|yml)?\s*", lines[index]) is None:
            index += 1
            continue
        yaml_lines: list[str] = []
        index += 1
        while index < len(lines) and _FENCE_CLOSE_RE.match(lines[index]) is None:
            yaml_lines.append(lines[index])
            index += 1
        if index < len(lines):
            try:
                parsed = yaml.safe_load("\n".join(yaml_lines))
            except yaml.YAMLError:
                parsed = None
            if isinstance(parsed, dict):
                documents.append(parsed)
        index += 1
    return documents


def _task_block_mapping(body: str) -> Mapping[str, Any]:
    task_block = _extract_task_block(body)
    if task_block is None:
        return {}
    try:
        parsed = yaml.safe_load(task_block)
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _extract_task_block(body: str) -> str | None:
    lines = body.splitlines()
    index = 0
    while index < len(lines):
        if _TASK_FENCE_OPEN_RE.match(lines[index]) is None:
            index += 1
            continue
        start = index + 1
        index = start
        while index < len(lines):
            if _FENCE_CLOSE_RE.match(lines[index]):
                return "\n".join(lines[start:index])
            index += 1
        return None
    return None


def _metadata_before_task(body: str) -> str:
    return body.split("```task", 1)[0]


def _is_pull_request_item(issue: Mapping[str, Any]) -> bool:
    if issue.get("pull_request") is not None:
        return True
    url = str(issue.get("url") or issue.get("html_url") or "")
    return "/pull/" in url or "/pulls/" in url


def _issue_number(issue: Mapping[str, Any]) -> int | None:
    number = issue.get("number")
    return number if isinstance(number, int) and not isinstance(number, bool) and number > 0 else None


def _label_names(labels: object) -> frozenset[str]:
    if not isinstance(labels, list):
        return frozenset()
    names: set[str] = set()
    for label in labels:
        if isinstance(label, str):
            names.add(label)
        elif isinstance(label, Mapping) and isinstance(label.get("name"), str):
            names.add(label["name"])
    return frozenset(names)
