#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

WT = Path('/home/agent/agent-dev/worktrees/skeleton/issue-1450')


def run(*args: str) -> str:
    cp = subprocess.run(args, cwd=WT, text=True, capture_output=True)
    if cp.returncode:
        raise SystemExit(f"FAILED: {' '.join(args)}\n{cp.stdout}{cp.stderr}")
    return cp.stdout.strip()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected 1 occurrence, found {count}')
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    text, count = re.subn(pattern, lambda _m: replacement, text, flags=re.DOTALL)
    if count != 1:
        raise SystemExit(f'{label}: expected 1 replacement, found {count}')
    return text


if run('git', 'branch', '--show-current') != 'runner/issue-1450':
    raise SystemExit('wrong branch')
if run('git', 'status', '--porcelain'):
    raise SystemExit('worktree is not clean')
run('git', 'fetch', 'origin', 'runner/issue-1450')
run('git', 'pull', '--ff-only', 'origin', 'runner/issue-1450')

p = WT / 'core/runner_retry_policy.py'
t = p.read_text()
t = replace_once(t, 'from dataclasses import dataclass\n', 'from dataclasses import dataclass, replace\n', 'dataclass import')
t = replace_once(t, '''class PriorBlockedReport:
    blocker_signature: str
    retry_attempt: int
    route: str | None = None
    override_token_hash: str | None = None
''', '''class PriorBlockedReport:
    blocker_signature: str
    retry_attempt: int
    route: str | None = None
    condition_signature: str | None = None
    override_token_hash: str | None = None
''', 'PriorBlockedReport')
t = replace_once(t, '''class RetryDecision:
    retry_decision: str
    retry_attempt: int
    blocker_signature: str
    route: str
    changed_condition: bool = False
''', '''class RetryDecision:
    retry_decision: str
    retry_attempt: int
    blocker_signature: str
    route: str
    condition_signature: str = ""
    changed_condition: bool = False
''', 'RetryDecision')
t = replace_once(t, '''        if self.next_required_action is not None:
            fields["next_required_action"] = self.next_required_action
''', '''        if self.condition_signature:
            fields["condition_signature"] = self.condition_signature
        if self.next_required_action is not None:
            fields["next_required_action"] = self.next_required_action
''', 'public fields')
t = regex_once(t, r'''def blocker_signature\(condition: RetryCondition\) -> str:
.*?


def parse_prior_blocked_reports''', '''def stable_condition_signature(condition: RetryCondition) -> str:
    route = normalize_route(condition.route)
    payload = {
        "route": route,
        "maintenance_task_id": _bounded_value(condition.maintenance_task_id),
        "allowed_files": _safe_files(condition.allowed_files),
        "expected_output_hash": hashlib.sha256(
            _bounded_value(condition.expected_output).encode("utf-8")
        ).hexdigest()[:16],
        "dependency_state": _bounded_value(condition.dependency_state),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def blocker_signature(condition: RetryCondition) -> str:
    payload = {
        "condition_signature": stable_condition_signature(condition),
        "blocker_reason": bounded_public_reason(condition.blocker_reason),
        "status_fields": _safe_status_fields(condition.status_fields),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def parse_prior_blocked_reports''', 'signature functions')
t = regex_once(t, r'''def parse_prior_blocked_reports\(comments: Iterable\[Mapping\[str, object\] \| str\]\) -> list\[PriorBlockedReport\]:
.*?


def _is_runner_authored_comment''', '''def parse_prior_blocked_reports(
    comments: Iterable[Mapping[str, object] | str],
) -> list[PriorBlockedReport]:
    reports: list[PriorBlockedReport] = []
    for comment in comments:
        body = comment if isinstance(comment, str) else comment.get("body")
        if not isinstance(body, str):
            continue
        if "BLOCKED" not in body and "NEEDS_OPERATOR" not in body:
            continue
        fields = _parse_public_fields(body)
        signature = fields.get("blocker_signature")
        route = fields.get("route")
        decision = fields.get("retry_decision")
        condition_signature = fields.get("condition_signature")
        if not signature or not re.fullmatch(r"[0-9a-f]{8,32}", signature):
            continue
        if route not in TASK_ROUTES or decision not in TERMINAL_RETRY_DECISIONS:
            continue
        if condition_signature is not None and not re.fullmatch(
            r"[0-9a-f]{8,32}", condition_signature
        ):
            continue
        try:
            attempt = max(1, int(fields.get("retry_attempt") or "1"))
        except ValueError:
            attempt = 1
        reports.append(
            PriorBlockedReport(
                blocker_signature=signature,
                retry_attempt=attempt,
                route=route,
                condition_signature=condition_signature,
                override_token_hash=fields.get("override_token_hash"),
            )
        )
    return reports


def _is_runner_authored_comment''', 'prior report parser')
t = regex_once(t, r'''def evaluate_retry_policy\(
.*?


def _next_required_action''', '''def evaluate_retry_policy(
    condition: RetryCondition,
    prior_reports: Iterable[PriorBlockedReport],
    override: RetryOverride | None = None,
) -> RetryDecision:
    route = normalize_route(condition.route)
    condition_signature = stable_condition_signature(condition)
    candidate_signature = blocker_signature(condition)
    relevant = [report for report in prior_reports if report.route in (None, route)]
    used_override_hashes = {
        report.override_token_hash for report in relevant if report.override_token_hash
    }
    if override is not None:
        if override.token_hash in used_override_hashes:
            return RetryDecision(
                retry_decision=NEEDS_OPERATOR,
                retry_attempt=max((r.retry_attempt for r in relevant), default=0) + 1,
                blocker_signature=candidate_signature,
                route=route,
                condition_signature=condition_signature,
                next_required_action="DIAGNOSE",
            )
        return RetryDecision(
            retry_decision=ALLOW_ONE_TIME_OVERRIDE,
            retry_attempt=max((r.retry_attempt for r in relevant), default=0) + 1,
            blocker_signature=candidate_signature,
            route=route,
            condition_signature=condition_signature,
            override_used=True,
            override_token_hash=override.token_hash,
        )
    if not relevant:
        return RetryDecision(
            retry_decision=ALLOW_FIRST_ATTEMPT,
            retry_attempt=1,
            blocker_signature=candidate_signature,
            route=route,
            condition_signature=condition_signature,
        )
    same_condition = [
        report for report in relevant
        if report.condition_signature == condition_signature
    ]
    if not same_condition:
        return RetryDecision(
            retry_decision=ALLOW_CHANGED_CONDITION,
            retry_attempt=1,
            blocker_signature=candidate_signature,
            route=route,
            condition_signature=condition_signature,
            changed_condition=True,
        )
    attempt = max(report.retry_attempt for report in same_condition) + 1
    latest_two = same_condition[-2:]
    if len(latest_two) == 2 and latest_two[0].blocker_signature == latest_two[1].blocker_signature:
        return RetryDecision(
            retry_decision=BLOCK_REPEATED_REASON,
            retry_attempt=attempt,
            blocker_signature=latest_two[-1].blocker_signature,
            route=route,
            condition_signature=condition_signature,
            next_required_action=_next_required_action(route),
        )
    if len(same_condition) >= 2:
        return RetryDecision(
            retry_decision=ALLOW_CHANGED_CONDITION,
            retry_attempt=attempt,
            blocker_signature=candidate_signature,
            route=route,
            condition_signature=condition_signature,
            changed_condition=True,
        )
    return RetryDecision(
        retry_decision=ALLOW_FIRST_ATTEMPT,
        retry_attempt=attempt,
        blocker_signature=candidate_signature,
        route=route,
        condition_signature=condition_signature,
    )


def _next_required_action''', 'evaluate policy')
t = regex_once(t, r'''def append_retry_fields\(report: str, decision: RetryDecision\) -> str:
.*\Z''', '''def _report_blocker_reason(report: str) -> str:
    patterns = (
        r"(?mi)^reason=(?P<reason>[A-Za-z0-9_.:-]+)\\s*$",
        r"(?mi)^Blocked marker:\\s*(?P<reason>[^\\n]+)$",
        r"(?mi)^Reason:\\s*(?P<reason>[^\\n]+)$",
        r"(?mi)^(?:BLOCKED|NEEDS_OPERATOR):\\s*(?P<reason>[^\\n]+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, report or "")
        if match is not None:
            return bounded_public_reason(match.group("reason"))
    return "unspecified_blocker"


def _report_blocker_signature(report: str, decision: RetryDecision) -> str:
    payload = {
        "condition_signature": decision.condition_signature or "legacy_condition",
        "blocker_reason": _report_blocker_reason(report),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def append_retry_fields(report: str, decision: RetryDecision) -> str:
    effective = replace(
        decision,
        blocker_signature=_report_blocker_signature(report, decision),
    )
    lines = [report.rstrip(), ""]
    lines.extend(f"{key}={value}" for key, value in effective.public_fields().items())
    if effective.override_used and effective.override_token_hash:
        lines.append(f"override_token_hash={effective.override_token_hash}")
    return "\\n".join(lines).rstrip()
''', 'append retry fields')
p.write_text(t)

p = WT / 'scripts/runner_poll_github_tasks.py'
t = p.read_text()
t = replace_once(t, '''    claimed = False
    runner_task: RunnerTask | None = None
    try:
''', '''    claimed = False
    runner_task: RunnerTask | None = None
    retry_decision: RetryDecision | None = None
    try:
''', 'retry decision init')
t = replace_once(t, '''                f"Runner error:\\n```\\n{exc}\\n```"
''', '''                f"Runner error:\\nReason: {type(exc).__name__}\\n```\\n{exc}\\n```"
''', 'finalization reason')
t = replace_once(t, '''                runner_task=runner_task,
                result_status="ERROR",
''', '''                runner_task=runner_task,
                result_status="ERROR",
                retry_decision=retry_decision,
''', 'finalization retry decision')
p.write_text(t)

p = WT / 'tests/test_runner_retry_policy.py'
t = p.read_text()
t = replace_once(t, '''        blocker_signature=decision.blocker_signature,
        route=decision.route,
    )
''', '''        blocker_signature=decision.blocker_signature,
        route=decision.route,
        condition_signature=decision.condition_signature,
    )
''', 'test helper')
if 'def test_actual_blocker_reason_controls_recorded_signature()' not in t:
    t += '''\n\ndef test_actual_blocker_reason_controls_recorded_signature() -> None:\n    condition = _condition()\n    decision = evaluate_retry_policy(condition, [])\n    first = parse_prior_blocked_reports([append_retry_fields("BLOCKED: codex_nonzero_exit", decision)])[0]\n    second = parse_prior_blocked_reports([append_retry_fields("BLOCKED: maintenance_failure", decision)])[0]\n    assert first.condition_signature == second.condition_signature\n    assert first.blocker_signature != second.blocker_signature\n\n\ndef test_two_identical_actual_reasons_block_before_third_execution() -> None:\n    condition = _condition()\n    first_decision = evaluate_retry_policy(condition, [])\n    first_report = append_retry_fields("BLOCKED: codex_nonzero_exit", first_decision)\n    second_decision = evaluate_retry_policy(condition, parse_prior_blocked_reports([first_report]))\n    second_report = append_retry_fields("BLOCKED: codex_nonzero_exit", second_decision)\n    third_decision = evaluate_retry_policy(condition, parse_prior_blocked_reports([first_report, second_report]))\n    assert third_decision.retry_decision == BLOCK_REPEATED_REASON\n    assert third_decision.retry_attempt == 3\n\n\ndef test_different_actual_reasons_allow_changed_retry() -> None:\n    condition = _condition()\n    first = append_retry_fields("BLOCKED: codex_nonzero_exit", evaluate_retry_policy(condition, []))\n    second_decision = evaluate_retry_policy(condition, parse_prior_blocked_reports([first]))\n    second = append_retry_fields("BLOCKED: maintenance_failure", second_decision)\n    third = evaluate_retry_policy(condition, parse_prior_blocked_reports([first, second]))\n    assert third.retry_decision == ALLOW_CHANGED_CONDITION\n    assert third.changed_condition is True\n\n\ndef test_structural_runner_fields_are_parsed_for_operator_authored_comments() -> None:\n    condition = _condition()\n    report = append_retry_fields("BLOCKED: synthetic_failure", evaluate_retry_policy(condition, []))\n    parsed = parse_prior_blocked_reports([{"author": {"login": "alanua"}, "body": report}])\n    assert len(parsed) == 1\n    assert parsed[0].condition_signature is not None\n'''
p.write_text(t)

p = WT / 'tests/test_runner_poll_github_tasks.py'
t = p.read_text()
if 'def test_block_issue_uses_actual_bounded_failure_reason_for_retry_signature()' not in t:
    t += '''\n\ndef test_block_issue_uses_actual_bounded_failure_reason_for_retry_signature() -> None:\n    body = "Expected Output: draft PR\\nAllowed Files:\\n- core/example.py"\n    condition = runner.retry_condition_for_issue(body, runner.ROUTE_CODE_GENERATION, None)\n    decision = runner.evaluate_retry_policy(condition, [])\n    comments: list[str] = []\n    with mock.patch.object(runner, "post_issue_comment", side_effect=lambda _number, comment: comments.append(comment)), mock.patch.object(runner, "set_issue_label"), mock.patch.object(runner, "notify_task_finished"), mock.patch.object(runner, "record_runner_executor_result", return_value=None):\n        runner.block_issue(1450, "Codex task failed:\\nReason: codex_nonzero_exit", retry_decision=decision)\n        runner.block_issue(1450, "Runner error:\\nReason: RuntimeError", retry_decision=decision)\n    reports = runner.parse_prior_blocked_reports(comments)\n    assert len(reports) == 2\n    assert reports[0].condition_signature == reports[1].condition_signature\n    assert reports[0].blocker_signature != reports[1].blocker_signature\n'''
p.write_text(t)

p = WT / 'schemas/runner_retry_policy.schema.json'
schema = json.loads(p.read_text())
if 'condition_signature' not in schema['required']:
    schema['required'].insert(schema['required'].index('blocker_signature'), 'condition_signature')
schema['properties']['condition_signature'] = {'type': 'string', 'pattern': '^[0-9a-f]{8,32}$'}
p.write_text(json.dumps(schema, indent=2) + '\n')

p = WT / 'docs/RUNNER_RETRY_POLICY.md'
t = p.read_text()
if '## Actual blocker binding' not in t:
    t += '''\n\n## Actual blocker binding\n\nThe stable condition signature represents route and bounded static task scope. The blocker signature is bound when a failure report is created, using the actual bounded reason or final blocked marker. Two consecutive reports with the same condition and blocker signatures stop the next execution before Codex or maintenance dispatch.\n'''
p.write_text(t)

run('python3', '-m', 'pytest', '-q', 'tests/test_runner_retry_policy.py', 'tests/test_runner_poll_github_tasks.py')
run('python3', '-m', 'pytest', '-q')
run('python3', '-m', 'py_compile', 'scripts/runner_poll_github_tasks.py', 'core/runner_retry_policy.py')
run('git', 'diff', '--check')
files = [
    'scripts/runner_poll_github_tasks.py',
    'core/runner_retry_policy.py',
    'schemas/runner_retry_policy.schema.json',
    'tests/test_runner_retry_policy.py',
    'tests/test_runner_poll_github_tasks.py',
    'docs/RUNNER_RETRY_POLICY.md',
]
run('git', 'add', *files)
run('git', 'diff', '--cached', '--check')
run('git', 'commit', '-m', 'Fix Runner blocker signatures to use actual failure reasons')
run('git', 'push', 'origin', 'HEAD:runner/issue-1450')
print('DONE')
print('HEAD=' + run('git', 'rev-parse', 'HEAD'))
print(run('git', 'diff', '--stat', 'origin/main...HEAD'))
