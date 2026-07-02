from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping


ROUTE_CODE_GENERATION = "code_generation"
ROUTE_PUBLISH_ONLY = "publish_only"
ROUTE_RUNTIME_ONLY = "runtime_only"

ALLOW_FIRST_ATTEMPT = "ALLOW_FIRST_ATTEMPT"
ALLOW_CHANGED_CONDITION = "ALLOW_CHANGED_CONDITION"
ALLOW_ONE_TIME_OVERRIDE = "ALLOW_ONE_TIME_OVERRIDE"
BLOCK_REPEATED_REASON = "BLOCK_REPEATED_REASON"
BLOCK_ROUTE_MISMATCH = "BLOCK_ROUTE_MISMATCH"
NEEDS_OPERATOR = "NEEDS_OPERATOR"

TERMINAL_RETRY_DECISIONS = frozenset(
    (
        ALLOW_FIRST_ATTEMPT,
        ALLOW_CHANGED_CONDITION,
        ALLOW_ONE_TIME_OVERRIDE,
        BLOCK_REPEATED_REASON,
        BLOCK_ROUTE_MISMATCH,
        NEEDS_OPERATOR,
    )
)
TASK_ROUTES = frozenset(
    (ROUTE_CODE_GENERATION, ROUTE_PUBLISH_ONLY, ROUTE_RUNTIME_ONLY)
)

_FIELD_RE = re.compile(r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_ -]{0,80})=(?P<value>\S.*)$")
_BODY_FIELD_RE = re.compile(
    r"^\s*(?P<key>[A-Za-z][A-Za-z0-9 _-]{0,80}):\s*(?P<value>\S(?:.*\S)?)?\s*$"
)
_SECRETISH_RE = re.compile(
    r"(?i)(secret|token|password|credential|authorization|bearer|api[_-]?key|private[_-]?key)"
)
_PATHISH_RE = re.compile(r"(?:/[A-Za-z0-9._@+-]+){2,}|[A-Za-z]:\\")
_HEXISH_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_VOLATILE_RE = re.compile(
    r"(?i)\b(?:\d{4}-\d{2}-\d{2}|[0-2]?\d:[0-5]\d(?::[0-5]\d)?|"
    r"elapsed|duration|timestamp|traceback|stdout|stderr|command output)\b"
)
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,120}$")
PLACEHOLDER_EXPECTED_OUTPUTS = frozenset(
    {
        "todo",
        "tbd",
        "n/a",
        "na",
        "none",
        "placeholder",
        "{expected_output}",
        "<expected_output>",
        "expected_output",
    }
)


@dataclass(frozen=True)
class RetryCondition:
    route: str
    maintenance_task_id: str | None = None
    allowed_files: tuple[str, ...] = ()
    expected_output: str | None = None
    dependency_state: str | None = None
    blocker_reason: str | None = None
    status_fields: Mapping[str, str] | None = None


@dataclass(frozen=True)
class PriorBlockedReport:
    blocker_signature: str
    retry_attempt: int
    route: str | None = None
    override_token_hash: str | None = None


@dataclass(frozen=True)
class RetryOverride:
    token_hash: str
    reason: str


@dataclass(frozen=True)
class ExpectedOutputValidation:
    accepted: bool
    reason: str | None = None


@dataclass(frozen=True)
class RetryDecision:
    retry_decision: str
    retry_attempt: int
    blocker_signature: str
    route: str
    changed_condition: bool = False
    override_used: bool = False
    next_required_action: str | None = None
    override_token_hash: str | None = None

    def public_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {
            "route": self.route,
            "retry_decision": self.retry_decision,
            "retry_attempt": self.retry_attempt,
            "blocker_signature": self.blocker_signature,
            "changed_condition": str(self.changed_condition).lower(),
            "override_used": str(self.override_used).lower(),
        }
        if self.next_required_action is not None:
            fields["next_required_action"] = self.next_required_action
        return fields


def normalize_route(route: str) -> str:
    normalized = (route or "").strip().lower()
    if normalized not in TASK_ROUTES:
        raise ValueError(f"unsupported runner task route: {route!r}")
    return normalized


def bounded_public_reason(reason: str | None) -> str:
    text = (reason or "unspecified_blocker").strip().lower()
    text = _PATHISH_RE.sub(" path ", text)
    text = _HEXISH_RE.sub(" digest ", text)
    text = _NUMBER_RE.sub(" n ", text)
    text = re.sub(r"[^a-z0-9_.:-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._:-")
    if not text or _SECRETISH_RE.search(text) or _VOLATILE_RE.search(text):
        return "redacted_blocker"
    return text[:80]


def _bounded_value(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _SECRETISH_RE.search(text):
        return "redacted"
    text = _PATHISH_RE.sub("path", text)
    text = _HEXISH_RE.sub("digest", text)
    text = _NUMBER_RE.sub("n", text)
    text = re.sub(r"\s+", " ", text)
    return text[:160]


def _safe_files(files: Iterable[str]) -> tuple[str, ...]:
    safe: list[str] = []
    for file_name in files:
        text = str(file_name or "").strip()
        if not text or text.startswith("/") or ".." in text.split("/"):
            safe.append("redacted_path")
            continue
        safe.append(_bounded_value(text))
    return tuple(sorted(set(safe)))


def _safe_status_fields(fields: Mapping[str, str] | None) -> dict[str, str]:
    if not fields:
        return {}
    safe: dict[str, str] = {}
    for key, value in sorted(fields.items()):
        normalized_key = re.sub(r"[^a-z0-9_]+", "_", str(key).strip().lower())[:60]
        if not normalized_key or _SECRETISH_RE.search(normalized_key):
            continue
        safe[normalized_key] = _bounded_value(value)
    return safe


def blocker_signature(condition: RetryCondition) -> str:
    route = normalize_route(condition.route)
    payload = {
        "route": route,
        "maintenance_task_id": _bounded_value(condition.maintenance_task_id),
        "allowed_files": _safe_files(condition.allowed_files),
        "expected_output_hash": hashlib.sha256(
            _bounded_value(condition.expected_output).encode("utf-8")
        ).hexdigest()[:16],
        "dependency_state": _bounded_value(condition.dependency_state),
        "blocker_reason": bounded_public_reason(condition.blocker_reason),
        "status_fields": _safe_status_fields(condition.status_fields),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def parse_prior_blocked_reports(comments: Iterable[Mapping[str, object] | str]) -> list[PriorBlockedReport]:
    reports: list[PriorBlockedReport] = []
    for comment in comments:
        if isinstance(comment, Mapping) and not _is_runner_authored_comment(comment):
            continue
        body = comment if isinstance(comment, str) else comment.get("body")
        if not isinstance(body, str):
            continue
        if "BLOCKED" not in body and "NEEDS_OPERATOR" not in body:
            continue
        fields = _parse_public_fields(body)
        signature = fields.get("blocker_signature")
        if not signature or not re.fullmatch(r"[0-9a-f]{8,32}", signature):
            continue
        attempt_text = fields.get("retry_attempt") or "1"
        try:
            attempt = max(1, int(attempt_text))
        except ValueError:
            attempt = 1
        reports.append(
            PriorBlockedReport(
                blocker_signature=signature,
                retry_attempt=attempt,
                route=fields.get("route"),
                override_token_hash=fields.get("override_token_hash"),
            )
        )
    return reports


def _is_runner_authored_comment(comment: Mapping[str, object]) -> bool:
    author = comment.get("author")
    if not isinstance(author, Mapping):
        return True
    login = str(author.get("login") or "").lower()
    if not login:
        return True
    return "runner" in login or "github-actions" in login or login.endswith("[bot]")


def _parse_public_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in body.splitlines():
        match = _FIELD_RE.match(line)
        if match is None:
            continue
        key = match.group("key").strip().lower().replace(" ", "_").replace("-", "_")
        fields[key] = match.group("value").strip()
    return fields


def extract_retry_override(issue_body: str) -> RetryOverride | None:
    token = _body_field(issue_body, "Retry Override")
    reason = _body_field(issue_body, "Retry Reason")
    if token is None:
        return None
    token = token.strip()
    if _SAFE_TOKEN_RE.fullmatch(token) is None:
        return None
    bounded_reason = bounded_public_reason(reason)
    if bounded_reason in {"unspecified_blocker", "redacted_blocker"}:
        return None
    return RetryOverride(
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest()[:16],
        reason=bounded_reason,
    )


def expected_output_validation(value: object) -> ExpectedOutputValidation:
    if value is None:
        return ExpectedOutputValidation(False, "missing_expected_output")
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = value
    else:
        return ExpectedOutputValidation(False, "invalid_expected_output")

    normalized = [item.strip() for item in items]
    if not normalized or any(not item for item in normalized):
        return ExpectedOutputValidation(False, "empty_expected_output")
    if any(item.lower() in PLACEHOLDER_EXPECTED_OUTPUTS for item in normalized):
        return ExpectedOutputValidation(False, "placeholder_expected_output")
    return ExpectedOutputValidation(True)


def one_time_override_hash(override: dict[str, Any]) -> str:
    encoded = json.dumps(override, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()[:16]


def _body_field(body: str, field: str) -> str | None:
    wanted = field.strip().lower()
    for line in (body or "").splitlines():
        match = _BODY_FIELD_RE.match(line)
        if match is None:
            continue
        if match.group("key").strip().lower() == wanted:
            value = match.group("value")
            return value.strip() if isinstance(value, str) else ""
    return None


def evaluate_retry_policy(
    condition: RetryCondition,
    prior_reports: Iterable[PriorBlockedReport],
    override: RetryOverride | None = None,
) -> RetryDecision:
    route = normalize_route(condition.route)
    signature = blocker_signature(condition)
    prior_report_list = list(prior_reports)
    relevant = [report for report in prior_report_list if report.route in (None, route)]
    route_mismatches = [
        report for report in prior_report_list if report.route not in (None, route)
    ]
    if route_mismatches and len(relevant) < len(prior_report_list):
        return RetryDecision(
            retry_decision=ALLOW_CHANGED_CONDITION,
            retry_attempt=1,
            blocker_signature=signature,
            route=route,
            changed_condition=True,
        )

    used_override_hashes = {
        report.override_token_hash for report in relevant if report.override_token_hash
    }
    if override is not None:
        if override.token_hash in used_override_hashes:
            return RetryDecision(
                retry_decision=NEEDS_OPERATOR,
                retry_attempt=max((r.retry_attempt for r in relevant), default=0) + 1,
                blocker_signature=signature,
                route=route,
                next_required_action="DIAGNOSE",
            )
        return RetryDecision(
            retry_decision=ALLOW_ONE_TIME_OVERRIDE,
            retry_attempt=max((r.retry_attempt for r in relevant), default=0) + 1,
            blocker_signature=signature,
            route=route,
            override_used=True,
            override_token_hash=override.token_hash,
        )

    if not relevant:
        return RetryDecision(
            retry_decision=ALLOW_FIRST_ATTEMPT,
            retry_attempt=1,
            blocker_signature=signature,
            route=route,
        )

    same_signature = [report for report in relevant if report.blocker_signature == signature]
    if not same_signature:
        return RetryDecision(
            retry_decision=ALLOW_CHANGED_CONDITION,
            retry_attempt=1,
            blocker_signature=signature,
            route=route,
            changed_condition=True,
        )

    attempt = max(report.retry_attempt for report in same_signature) + 1
    latest_two = relevant[-2:]
    if (
        len(latest_two) >= 2
        and latest_two[0].blocker_signature == signature
        and latest_two[1].blocker_signature == signature
    ):
        return RetryDecision(
            retry_decision=BLOCK_REPEATED_REASON,
            retry_attempt=attempt,
            blocker_signature=signature,
            route=route,
            next_required_action=_next_required_action(route),
        )

    return RetryDecision(
        retry_decision=ALLOW_FIRST_ATTEMPT,
        retry_attempt=attempt,
        blocker_signature=signature,
        route=route,
        changed_condition=False,
    )


def _next_required_action(route: str) -> str:
    if route == ROUTE_PUBLISH_ONLY:
        return "PUBLISH_ONLY"
    if route == ROUTE_RUNTIME_ONLY:
        return "RUNTIME_ONLY"
    return "DIAGNOSE"


def append_retry_fields(report: str, decision: RetryDecision) -> str:
    lines = [report.rstrip(), ""]
    lines.extend(f"{key}={value}" for key, value in decision.public_fields().items())
    if decision.override_used and decision.override_token_hash:
        lines.append(f"override_token_hash={decision.override_token_hash}")
    return "\n".join(lines).rstrip()
