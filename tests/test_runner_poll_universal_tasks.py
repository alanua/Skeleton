from __future__ import annotations

from unittest import mock

from scripts import runner_poll_universal_tasks as universal


def queue_issue(body: str) -> dict[str, object]:
    return {
        "number": 42,
        "body": body,
        "state": "OPEN",
        "closed": False,
        "author": {"login": "alanua"},
    }


def test_envelopes_only_skips_legacy_tasks() -> None:
    issue = queue_issue("Mode: RUNTIME_MAINTENANCE_TASK\n")
    with mock.patch.object(
        universal.legacy_runner,
        "get_ready_issues",
        return_value=[issue],
    ), mock.patch.object(
        universal,
        "_read_issue_with_author",
        return_value=issue,
    ), mock.patch.object(
        universal.legacy_runner,
        "process_issue",
    ) as process:
        count = universal.poll_once(include_legacy=False)

    assert count == 0
    process.assert_not_called()


def test_compatibility_mode_routes_legacy_tasks() -> None:
    issue = queue_issue("Mode: RUNTIME_MAINTENANCE_TASK\n")
    with mock.patch.object(
        universal.legacy_runner,
        "get_ready_issues",
        return_value=[issue],
    ), mock.patch.object(
        universal,
        "_read_issue_with_author",
        return_value=issue,
    ), mock.patch.object(
        universal.legacy_runner,
        "process_issue",
    ) as process:
        count = universal.poll_once(include_legacy=True)

    assert count == 1
    process.assert_called_once()


def test_task_envelope_route_never_invokes_legacy_executor() -> None:
    issue = queue_issue(
        "Mode: TASK_ENVELOPE\n"
        "Envelope Ref: task-001\n"
        + "Envelope SHA256: "
        + "a" * 64
        + "\n"
    )
    with mock.patch.object(
        universal.legacy_runner,
        "get_ready_issues",
        return_value=[issue],
    ), mock.patch.object(
        universal,
        "_read_issue_with_author",
        return_value=issue,
    ), mock.patch.object(
        universal,
        "_process_envelope_request",
    ) as process_envelope, mock.patch.object(
        universal.legacy_runner,
        "process_issue",
    ) as process_legacy:
        count = universal.poll_once(include_legacy=True)

    assert count == 1
    process_envelope.assert_called_once()
    process_legacy.assert_not_called()


def test_public_receipt_report_drops_unknown_values() -> None:
    report = universal._receipt_report(
        {
            "schema": "skeleton.runner.public_receipt.v1",
            "task_id": "task-001",
            "envelope_hash": "a" * 64,
            "evidence_hash": "b" * 64,
            "executor_class": "composite",
            "risk_class": "yellow",
            "privacy_class": "private",
            "status": "BLOCKED",
            "step_count": 1,
            "assertion_count": 1,
            "rollback_status": "DONE",
            "rollback_step_count": 1,
            "private_value": "must-not-appear",
        },
        status="BLOCKED",
    )

    assert "must-not-appear" not in report
    assert '"rollback_status": "DONE"' in report


def test_blocked_report_does_not_publish_exception_message() -> None:
    report = universal._blocked_report(
        RuntimeError("private path and operator-only value")
    )

    assert "private path" not in report
    assert "RuntimeError" in report
