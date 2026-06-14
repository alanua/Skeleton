from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import publish_task_issue as publisher


VALID_BODY = """```task
classification: YELLOW_LOCAL_PATCH
repo: alanua/Skeleton
intent: safe_task_issue_publisher
goal: add a safe publisher
scope:
- scripts/publish_task_issue.py
non_goals:
- no secrets
acceptance:
- publisher verifies read-back
```"""


def _write_body(tmp_path: Path, body: str = VALID_BODY) -> Path:
    path = tmp_path / "task.md"
    path.write_text(body, encoding="utf-8")
    return path


def _issue_json(*, body: str = VALID_BODY, number: int = 861) -> str:
    return json.dumps(
        {
            "number": number,
            "url": f"https://github.com/alanua/Skeleton/issues/{number}",
            "body": body,
        }
    )


def test_invalid_repo_is_rejected_before_gh_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body_file = _write_body(tmp_path)

    def fake_run_command(args: list[str]) -> tuple[int, str]:
        raise AssertionError(args)

    monkeypatch.setattr(publisher, "run_command", fake_run_command)

    with pytest.raises(publisher.PublishError, match="--repo"):
        publisher.publish_task_issue(
            repo="alanua/Skeleton extra",
            title="Safe task",
            body_file=body_file,
        )


def test_invalid_issue_number_is_rejected_before_gh_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body_file = _write_body(tmp_path)

    def fake_run_command(args: list[str]) -> tuple[int, str]:
        raise AssertionError(args)

    monkeypatch.setattr(publisher, "run_command", fake_run_command)

    with pytest.raises(publisher.PublishError, match="--issue"):
        publisher.publish_task_issue(
            repo="alanua/Skeleton",
            issue_number=0,
            body_file=body_file,
        )


def test_valid_body_creates_issue_with_body_file_verifies_then_marks_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body_file = _write_body(tmp_path)
    calls: list[list[str]] = []

    def fake_run_command(args: list[str]) -> tuple[int, str]:
        calls.append(args)
        if args[:3] == ["gh", "issue", "create"]:
            return 0, "https://github.com/alanua/Skeleton/issues/861\n"
        if args[:3] == ["gh", "issue", "view"]:
            return 0, _issue_json()
        if args[:3] == ["gh", "issue", "edit"] and "--add-label" in args:
            return 0, ""
        raise AssertionError(args)

    monkeypatch.setattr(publisher, "run_command", fake_run_command)

    issue = publisher.publish_task_issue(
        repo="alanua/Skeleton",
        title="Safe task",
        body_file=body_file,
    )

    assert issue.number == 861
    assert issue.url == "https://github.com/alanua/Skeleton/issues/861"
    create_call = calls[0]
    assert "--body-file" in create_call
    assert str(body_file) in create_call
    assert "--body" not in create_call
    assert calls[1][:3] == ["gh", "issue", "view"]
    assert calls[2][-2:] == ["--add-label", publisher.READY_LABEL]


def test_missing_closing_fence_is_rejected(tmp_path: Path) -> None:
    body_file = _write_body(tmp_path, VALID_BODY.removesuffix("\n```"))

    with pytest.raises(publisher.PublishError, match="closing ``` fence"):
        publisher.load_and_validate_body(body_file)


def test_missing_required_section_is_rejected(tmp_path: Path) -> None:
    body_file = _write_body(
        tmp_path,
        VALID_BODY.replace("non_goals:\n- no secrets\n", ""),
    )

    with pytest.raises(publisher.PublishError, match="non_goals"):
        publisher.load_and_validate_body(body_file)


def test_duplicate_required_section_is_rejected(tmp_path: Path) -> None:
    body_file = _write_body(
        tmp_path,
        VALID_BODY.replace(
            "acceptance:\n- publisher verifies read-back\n",
            "acceptance:\n- publisher verifies read-back\nacceptance:\n- duplicated\n",
        ),
    )

    with pytest.raises(publisher.PublishError, match="duplicate required section: acceptance"):
        publisher.load_and_validate_body(body_file)


def test_read_back_mismatch_fails_closed_without_ready_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body_file = _write_body(tmp_path)
    calls: list[list[str]] = []

    def fake_run_command(args: list[str]) -> tuple[int, str]:
        calls.append(args)
        if args[:3] == ["gh", "issue", "create"]:
            return 0, "https://github.com/alanua/Skeleton/issues/861\n"
        if args[:3] == ["gh", "issue", "view"]:
            return 0, _issue_json(body=VALID_BODY.removesuffix("```"))
        raise AssertionError(args)

    monkeypatch.setattr(publisher, "run_command", fake_run_command)

    with pytest.raises(publisher.PublishError, match="does not match"):
        publisher.publish_task_issue(
            repo="alanua/Skeleton",
            title="Safe task",
            body_file=body_file,
        )

    assert not any("--add-label" in call for call in calls)


@pytest.mark.parametrize(
    ("view_output", "message"),
    [
        ("", "empty JSON"),
        ("[]", "non-object JSON"),
        (json.dumps({"number": 861, "url": "https://github.com/alanua/Skeleton/issues/861"}), "valid issue body"),
        (json.dumps({"number": "861", "url": "https://github.com/alanua/Skeleton/issues/861", "body": VALID_BODY}), "valid issue number"),
        (json.dumps({"number": 861, "url": "", "body": VALID_BODY}), "valid issue URL"),
    ],
)
def test_invalid_read_back_json_fails_closed_without_ready_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    view_output: str,
    message: str,
) -> None:
    body_file = _write_body(tmp_path)
    calls: list[list[str]] = []

    def fake_run_command(args: list[str]) -> tuple[int, str]:
        calls.append(args)
        if args[:3] == ["gh", "issue", "create"]:
            return 0, "https://github.com/alanua/Skeleton/issues/861\n"
        if args[:3] == ["gh", "issue", "view"]:
            return 0, view_output
        raise AssertionError(args)

    monkeypatch.setattr(publisher, "run_command", fake_run_command)

    with pytest.raises(publisher.PublishError, match=message):
        publisher.publish_task_issue(
            repo="alanua/Skeleton",
            title="Safe task",
            body_file=body_file,
        )

    assert not any("--add-label" in call for call in calls)


def test_no_runner_ready_before_update_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body_file = _write_body(tmp_path)
    calls: list[list[str]] = []

    def fake_run_command(args: list[str]) -> tuple[int, str]:
        calls.append(args)
        if args[:3] == ["gh", "issue", "edit"] and "--body-file" in args:
            assert "--add-label" not in args
            return 0, ""
        if args[:3] == ["gh", "issue", "view"]:
            assert not any("--add-label" in call for call in calls)
            return 0, _issue_json(number=862)
        if args[:3] == ["gh", "issue", "edit"] and "--add-label" in args:
            return 0, ""
        raise AssertionError(args)

    monkeypatch.setattr(publisher, "run_command", fake_run_command)

    issue = publisher.publish_task_issue(
        repo="alanua/Skeleton",
        issue_number=862,
        body_file=body_file,
    )

    assert issue.number == 862
    assert calls[0][:3] == ["gh", "issue", "edit"]
    assert "--body-file" in calls[0]
    assert calls[1][:3] == ["gh", "issue", "view"]
    assert calls[2][-2:] == ["--add-label", publisher.READY_LABEL]


def test_empty_body_file_is_rejected(tmp_path: Path) -> None:
    body_file = _write_body(tmp_path, "\n")

    with pytest.raises(publisher.PublishError, match="empty"):
        publisher.load_and_validate_body(body_file)
