from __future__ import annotations

from dataclasses import dataclass, field

import pytest

import scripts.runner_vnext_poll as poll


@dataclass
class _Source:
    repository: str = "alanua/Skeleton"


@dataclass
class _Item:
    issue: dict
    source: _Source = field(default_factory=_Source)

    @property
    def source_repository(self) -> str:
        return self.source.repository

    @property
    def issue_number(self) -> int:
        return int(self.issue["number"])


def test_validation_task_binds_exact_pr_head_base_and_files() -> None:
    head = "a" * 40
    base = "b" * 40
    body = f"""Repository: alanua/Skeleton
Pull Request: 77
Expected Head SHA: {head}
Expected Base SHA: {base}
Validation Profile: full_pytest
Allowed Files:
- core/a.py
- tests/test_a.py
Intent: exact validation
"""
    task, pr_number = poll._validation_task(_Item({"number": 1, "body": body}), body)

    assert pr_number == 77
    assert task.repo == "alanua/Skeleton"
    assert task.base_sha == head
    assert task.payload["expected_head_sha"] == head
    assert task.payload["expected_base_sha"] == base
    assert task.payload["profile"] == "full_pytest"
    assert task.allowed_files == ("core/a.py", "tests/test_a.py")
    assert task.requested_capabilities == ("repository_read", "test_execution")


def test_publication_task_binds_retained_worktree_head(monkeypatch: pytest.MonkeyPatch) -> None:
    head = "c" * 40
    monkeypatch.setattr(poll, "_worktree_head_ref", lambda issue: f"git:{head}")
    body = """Target Repository: alanua/Skeleton
Source Issue: 99
Output Branch: runner/issue-99
Allowed Files:
- core/a.py
- tests/test_a.py
Intent: exact publication
"""
    task, source_issue = poll._publication_task(_Item({"number": 2, "body": body}), body)

    assert source_issue == 99
    assert task.base_sha == head
    assert task.branch == "runner/issue-99"
    assert task.payload["source_issue"] == 99
    assert task.allowed_files == ("core/a.py", "tests/test_a.py")
    assert "publish_pull_request" in task.requested_capabilities


def test_dispatch_has_no_untyped_legacy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _Item({"number": 3, "body": ""})
    monkeypatch.setattr(poll.legacy, "extract_runtime_maintenance_task_id", lambda body: (False, None))
    monkeypatch.setattr(
        poll.legacy,
        "extract_telegram_approved_pr_merge_request",
        lambda body: (False, None, None),
    )
    monkeypatch.setattr(
        poll.legacy,
        "extract_runner_task",
        lambda body, default_repository: (None, None),
    )

    with pytest.raises(poll.VNextPollerError) as exc:
        poll.dispatch_item(item)

    assert exc.value.reason_code == "VNEXT_TYPED_TASK_REQUIRED"


def test_dispatch_routes_validation_to_vnext_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _Item({"number": 4, "body": "validation"})
    called: list[int] = []
    monkeypatch.setattr(
        poll.legacy,
        "extract_runtime_maintenance_task_id",
        lambda body: (True, poll.legacy.VALIDATE_PR_BRANCH),
    )
    monkeypatch.setattr(
        poll.legacy,
        "extract_telegram_approved_pr_merge_request",
        lambda body: (False, None, None),
    )
    monkeypatch.setattr(
        poll.legacy,
        "extract_runner_task",
        lambda body, default_repository: (None, None),
    )
    monkeypatch.setattr(
        poll,
        "_dispatch_validation",
        lambda routed_item, body, workdir: called.append(routed_item.issue_number),
    )
    monkeypatch.setattr(
        poll.legacy,
        "process_issue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy fallback forbidden")),
    )

    poll.dispatch_item(item)

    assert called == [4]


def test_unsigned_merge_is_rejected_before_legacy_mechanics(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Merge:
        pr_number = 10
        approved_head_sha = "d" * 40
        callback_digest = "0123456789ab"

    item = _Item({"number": 5, "body": "merge"})
    monkeypatch.setattr(poll.legacy, "telegram_approve_digest_is_signed", lambda request: False)
    monkeypatch.setattr(
        poll.legacy,
        "process_issue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy mechanics forbidden")),
    )

    with pytest.raises(poll.VNextPollerError) as exc:
        poll._dispatch_merge(item, "merge", _Merge(), None)

    assert exc.value.reason_code == "VNEXT_MERGE_SIGNED_APPROVAL_REQUIRED"
