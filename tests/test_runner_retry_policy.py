from __future__ import annotations

from core.runner_retry_policy import (
    ALLOW_CHANGED_CONDITION,
    ALLOW_FIRST_ATTEMPT,
    ALLOW_ONE_TIME_OVERRIDE,
    BLOCK_REPEATED_REASON,
    NEEDS_OPERATOR,
    ROUTE_CODE_GENERATION,
    RetryCondition,
    append_retry_fields,
    blocker_signature,
    evaluate_retry_policy,
    extract_retry_override,
    parse_prior_blocked_reports,
)


def _condition(**updates: object) -> RetryCondition:
    values = {
        "route": ROUTE_CODE_GENERATION,
        "allowed_files": ("core/example.py", "tests/test_example.py"),
        "expected_output": "draft PR with focused tests",
        "blocker_reason": "executor_invocation",
    }
    values.update(updates)
    return RetryCondition(**values)


def _blocked_comment(condition: RetryCondition, attempt: int = 1, extra: str = "") -> str:
    decision = evaluate_retry_policy(condition, [])
    decision = decision.__class__(
        retry_decision=decision.retry_decision,
        retry_attempt=attempt,
        blocker_signature=decision.blocker_signature,
        route=decision.route,
        condition_signature=decision.condition_signature,
    )
    return append_retry_fields("BLOCKED: synthetic blocker", decision) + extra


def test_first_attempt_is_allowed() -> None:
    decision = evaluate_retry_policy(_condition(), [])

    assert decision.retry_decision == ALLOW_FIRST_ATTEMPT
    assert decision.retry_attempt == 1


def test_one_prior_blocker_permits_bounded_second_attempt() -> None:
    condition = _condition()
    prior = parse_prior_blocked_reports([_blocked_comment(condition)])

    decision = evaluate_retry_policy(condition, prior)

    assert decision.retry_decision == ALLOW_FIRST_ATTEMPT
    assert decision.retry_attempt == 2


def test_two_identical_blocker_signatures_block() -> None:
    condition = _condition()
    prior = parse_prior_blocked_reports(
        [_blocked_comment(condition, 1), _blocked_comment(condition, 2)]
    )

    decision = evaluate_retry_policy(condition, prior)

    assert decision.retry_decision == BLOCK_REPEATED_REASON
    assert decision.retry_attempt == 3
    assert decision.next_required_action == "DIAGNOSE"


def test_changed_stable_condition_permits_retry_and_new_signature() -> None:
    old = _condition(expected_output="draft PR with focused tests")
    new = _condition(expected_output="draft PR with focused and full tests")
    prior = parse_prior_blocked_reports([_blocked_comment(old, 1), _blocked_comment(old, 2)])

    decision = evaluate_retry_policy(new, prior)

    assert decision.retry_decision == ALLOW_CHANGED_CONDITION
    assert decision.changed_condition is True
    assert decision.blocker_signature != blocker_signature(old)


def test_cosmetic_issue_body_edits_do_not_reset_guard() -> None:
    condition = _condition()
    prior = parse_prior_blocked_reports(
        [
            _blocked_comment(condition, 1, "\n\nEdited punctuation only."),
            _blocked_comment(condition, 2, "\n\nWhitespace changed."),
        ]
    )

    decision = evaluate_retry_policy(condition, prior)

    assert decision.retry_decision == BLOCK_REPEATED_REASON


def test_one_time_override_works_exactly_once() -> None:
    condition = _condition()
    prior = parse_prior_blocked_reports(
        [_blocked_comment(condition, 1), _blocked_comment(condition, 2)]
    )
    override = extract_retry_override(
        "Retry Override: opaque-token-1\nRetry Reason: dependency_updated"
    )

    decision = evaluate_retry_policy(condition, prior, override)

    assert decision.retry_decision == ALLOW_ONE_TIME_OVERRIDE
    assert decision.override_used is True
    assert decision.override_token_hash is not None


def test_reused_override_token_is_rejected() -> None:
    condition = _condition()
    override = extract_retry_override(
        "Retry Override: opaque-token-1\nRetry Reason: dependency_updated"
    )
    assert override is not None
    used = append_retry_fields(
        "BLOCKED: synthetic blocker",
        evaluate_retry_policy(condition, [], override),
    )
    prior = parse_prior_blocked_reports([used])

    decision = evaluate_retry_policy(condition, prior, override)

    assert decision.retry_decision == NEEDS_OPERATOR
    assert decision.override_used is False


def test_signature_redacts_or_ignores_paths_secrets_and_volatile_output() -> None:
    base = _condition(
        blocker_reason="command failed in /home/agent/private/path at 2026-07-02 10:04",
        status_fields={
            "stderr": "token=super-secret-value",
            "error_class": "FileNotFoundError",
            "quantity": "12345.67",
        },
    )
    same_stable = _condition(
        blocker_reason="command failed in /tmp/other/path at 2026-07-03 11:05",
        status_fields={
            "stderr": "token=different-secret-value",
            "error_class": "FileNotFoundError",
            "quantity": "1.0",
        },
    )

    assert blocker_signature(base) == blocker_signature(same_stable)



def test_actual_blocker_reason_controls_recorded_signature() -> None:
    condition = _condition()
    decision = evaluate_retry_policy(condition, [])

    first = parse_prior_blocked_reports(
        [append_retry_fields("BLOCKED: codex_nonzero_exit", decision)]
    )[0]
    second = parse_prior_blocked_reports(
        [append_retry_fields("BLOCKED: maintenance_failure", decision)]
    )[0]

    assert first.condition_signature == second.condition_signature
    assert first.blocker_signature != second.blocker_signature


def test_two_identical_actual_reasons_block_before_third_execution() -> None:
    condition = _condition()

    first_decision = evaluate_retry_policy(condition, [])
    first_report = append_retry_fields("BLOCKED: codex_nonzero_exit", first_decision)

    second_decision = evaluate_retry_policy(
        condition, parse_prior_blocked_reports([first_report])
    )
    second_report = append_retry_fields("BLOCKED: codex_nonzero_exit", second_decision)

    third_decision = evaluate_retry_policy(
        condition, parse_prior_blocked_reports([first_report, second_report])
    )

    assert third_decision.retry_decision == BLOCK_REPEATED_REASON
    assert third_decision.retry_attempt == 3


def test_different_actual_reasons_allow_changed_condition_retry() -> None:
    condition = _condition()

    first_decision = evaluate_retry_policy(condition, [])
    first_report = append_retry_fields("BLOCKED: codex_nonzero_exit", first_decision)

    second_decision = evaluate_retry_policy(
        condition, parse_prior_blocked_reports([first_report])
    )
    second_report = append_retry_fields("BLOCKED: maintenance_failure", second_decision)

    third_decision = evaluate_retry_policy(
        condition, parse_prior_blocked_reports([first_report, second_report])
    )

    assert third_decision.retry_decision == ALLOW_CHANGED_CONDITION
    assert third_decision.changed_condition is True


def test_machine_retry_fields_are_parsed_for_operator_posted_comments() -> None:
    condition = _condition()
    report = append_retry_fields(
        "BLOCKED: synthetic_operator_visible_failure",
        evaluate_retry_policy(condition, []),
    )

    parsed = parse_prior_blocked_reports(
        [{"author": {"login": "alanua"}, "body": report}],
        trusted_author_logins={"alanua"},
    )

    assert len(parsed) == 1
    assert parsed[0].condition_signature is not None



def test_untrusted_comment_cannot_create_retry_history() -> None:
    condition = _condition()
    report = append_retry_fields(
        "BLOCKED: synthetic_untrusted_failure",
        evaluate_retry_policy(condition, []),
    )

    parsed = parse_prior_blocked_reports(
        [{"author": {"login": "untrusted-collaborator"}, "body": report}],
        trusted_author_logins={"alanua"},
    )

    assert parsed == []


def test_untrusted_comments_cannot_force_repeated_blocker() -> None:
    condition = _condition()
    first = append_retry_fields(
        "BLOCKED: forged_failure",
        evaluate_retry_policy(condition, []),
    )
    second = append_retry_fields(
        "BLOCKED: forged_failure",
        evaluate_retry_policy(condition, []),
    )
    forged = [
        {"author": {"login": "untrusted-collaborator"}, "body": first},
        {"author": {"login": "untrusted-collaborator"}, "body": second},
    ]

    decision = evaluate_retry_policy(
        condition,
        parse_prior_blocked_reports(
            forged,
            trusted_author_logins={"alanua"},
        ),
    )

    assert decision.retry_decision == ALLOW_FIRST_ATTEMPT
    assert decision.retry_attempt == 1


def test_untrusted_comment_cannot_mark_override_token_used() -> None:
    condition = _condition()
    override = extract_retry_override(
        "Retry Override: opaque-token-1\nRetry Reason: dependency_updated"
    )
    assert override is not None
    forged_report = append_retry_fields(
        "BLOCKED: forged_override_use",
        evaluate_retry_policy(condition, [], override),
    )

    prior = parse_prior_blocked_reports(
        [
            {
                "author": {"login": "untrusted-collaborator"},
                "body": forged_report,
            }
        ],
        trusted_author_logins={"alanua"},
    )
    decision = evaluate_retry_policy(condition, prior, override)

    assert decision.retry_decision == ALLOW_ONE_TIME_OVERRIDE
    assert decision.override_used is True


def test_mapping_comment_without_author_is_rejected() -> None:
    condition = _condition()
    report = append_retry_fields(
        "BLOCKED: missing_author",
        evaluate_retry_policy(condition, []),
    )

    assert parse_prior_blocked_reports(
        [{"body": report}],
        trusted_author_logins={"alanua"},
    ) == []



def test_runner_like_and_bot_like_untrusted_authors_are_rejected() -> None:
    condition = _condition()
    report = append_retry_fields(
        "BLOCKED: forged_trusted_identity",
        evaluate_retry_policy(condition, []),
    )

    comments = [
        {"author": {"login": "evil-runner-service"}, "body": report},
        {"author": {"login": "evil-app[bot]"}, "body": report},
    ]

    assert parse_prior_blocked_reports(
        comments,
        trusted_author_logins={"alanua", "github-actions[bot]"},
    ) == []
