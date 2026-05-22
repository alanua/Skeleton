from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from unittest import mock
import urllib.error

import pytest

from scripts import telegram_callback_poller as poller


CALLBACK_HMAC_SECRET = "callback-hmac-test-secret"
HEAD_SHA = "deadbeef0123456789abcdef0123456789abcdef"


def signed_callback_data(
    action: str = "approve",
    *,
    pr_number: int = 120,
    head_marker: str = "deadbeef",
) -> str:
    digest = poller._callback_hmac_digest(
        action=action,
        pr_number=pr_number,
        head_marker=head_marker,
        hmac_secret=CALLBACK_HMAC_SECRET,
    )
    return f"tpr1:{action}:p{pr_number}:{head_marker}:{digest}"


CALLBACK_DATA = signed_callback_data()


def query(
    callback_data: str = CALLBACK_DATA,
    *,
    callback_id: str = "callback-query-1",
) -> dict[str, str]:
    return {"id": callback_id, "data": callback_data}


def response(payload: object | None = None) -> mock.MagicMock:
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    handle = mock.MagicMock()
    handle.__enter__.return_value = handle
    handle.read.return_value = body
    return handle


def request_body(request: mock.MagicMock) -> dict[str, object]:
    return json.loads(request.data.decode("utf-8"))


def github_pr_state(*, number: int = 120, head_sha: str = HEAD_SHA) -> dict[str, object]:
    return {"number": number, "head": {"sha": head_sha}}


def test_parses_valid_callback_data() -> None:
    parsed = poller.parse_callback_data(CALLBACK_DATA)

    assert parsed == poller.ParsedCallback(
        action="approve",
        pr_number=120,
        head_marker="deadbeef",
        digest=CALLBACK_DATA.rsplit(":", 1)[-1],
    )


@pytest.mark.parametrize(
    "callback_data",
    (
        "wrong:approve:p120:deadbeef:0123456789ab",
        "tpr1:merge:p120:deadbeef:0123456789ab",
        "tpr1:approve:p0:deadbeef:0123456789ab",
        "tpr1:approve:pnope:deadbeef:0123456789ab",
        "tpr1:approve:p120:notsha00:0123456789ab",
        "tpr1:approve:p120:deadbeef:notdigest000",
    ),
)
def test_rejects_malformed_callback_parts(callback_data: str) -> None:
    with pytest.raises(ValueError):
        poller.parse_callback_data(callback_data)


def test_malformed_callback_is_blocked_without_github_write() -> None:
    with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "github-secret"}, clear=True), mock.patch.object(
        poller.urllib.request,
        "urlopen",
    ) as urlopen:
        result = poller.handle_callback_query(query("tpr1:merge:p120:deadbeef:0123456789ab"))

    assert result["status"] == "blocked"
    assert result["comment_posted"] is False
    assert result["github"] == "not_called"
    urlopen.assert_not_called()


def test_dry_run_does_not_call_urllib() -> None:
    with mock.patch.dict(
        os.environ,
        {"GITHUB_TOKEN": "github-secret", "SKELETON_TG_BOT": "telegram-secret"},
        clear=True,
    ), mock.patch.object(poller.urllib.request, "urlopen") as urlopen:
        result = poller.handle_callback_query(query(), dry_run=True)

    urlopen.assert_not_called()
    assert result["status"] == "dry_run"
    assert result["comment_posted"] is False


def test_missing_github_token_returns_skipped_no_post_result() -> None:
    with mock.patch.dict(
        os.environ, {poller.CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET}, clear=True
    ), mock.patch.object(
        poller.urllib.request, "urlopen"
    ) as urlopen:
        result = poller.handle_callback_query(query())

    urlopen.assert_not_called()
    assert result["status"] == "skipped"
    assert result["github"] == "skipped_missing_token"
    assert result["comment_posted"] is False


def test_missing_callback_hmac_secret_blocks_live_callback_without_github_write() -> None:
    with mock.patch.dict(
        os.environ, {"GITHUB_TOKEN": "github-secret"}, clear=True
    ), mock.patch.object(poller.urllib.request, "urlopen") as urlopen:
        result = poller.handle_callback_query(query())

    assert result["status"] == "blocked"
    assert result["github"] == "not_called"
    assert poller.CALLBACK_HMAC_ENV in str(result["reason"])
    urlopen.assert_not_called()


def test_invalid_callback_hmac_blocks_live_callback_without_github_write() -> None:
    forged = "tpr1:approve:p120:deadbeef:0123456789ab"
    with mock.patch.dict(
        os.environ,
        {
            "GITHUB_TOKEN": "github-secret",
            poller.CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET,
        },
        clear=True,
    ), mock.patch.object(poller.urllib.request, "urlopen") as urlopen:
        result = poller.handle_callback_query(query(forged))

    assert result["status"] == "blocked"
    assert result["github"] == "not_called"
    assert "HMAC" in str(result["reason"])
    urlopen.assert_not_called()


def test_poll_once_skips_without_telegram_token(tmp_path: Path) -> None:
    state_path = tmp_path / "callback-state.json"
    with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
        poller.urllib.request, "urlopen"
    ) as urlopen:
        result = poller.poll_once(state_path=state_path)

    urlopen.assert_not_called()
    assert result == {
        "status": "skipped_missing_telegram_token",
        "updates_seen": 0,
        "callbacks_seen": 0,
        "callbacks_handled": 0,
        "offset": None,
    }


def test_poll_once_reads_updates_processes_callback_and_advances_offset(tmp_path: Path) -> None:
    state_path = tmp_path / "callback-state.json"
    update_payload = {"result": [{"update_id": 41, "callback_query": query()}]}
    with mock.patch.dict(os.environ, {"SKELETON_TG_BOT": "telegram-secret"}, clear=True), mock.patch.object(
        poller.urllib.request,
        "urlopen",
        side_effect=(response(update_payload), response()),
    ) as urlopen:
        result = poller.poll_once(state_path=state_path)

    get_updates_request, answer_request = [call.args[0] for call in urlopen.call_args_list]
    assert get_updates_request.get_method() == "GET"
    assert "/getUpdates?" in get_updates_request.full_url
    assert "allowed_updates=" in get_updates_request.full_url
    assert answer_request.full_url.endswith("/bottelegram-secret/answerCallbackQuery")
    assert result == {
        "status": "polled",
        "updates_seen": 1,
        "callbacks_seen": 1,
        "callbacks_handled": 1,
        "callbacks_duplicate": 0,
        "offset": 42,
    }
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "handled_callback_data": [CALLBACK_DATA],
        "handled_callback_ids": ["callback-query-1"],
        "offset": 42,
    }


@pytest.mark.parametrize(
    ("answer_error", "answer_error_name"),
    (
        (
            urllib.error.HTTPError(
                "https://api.telegram.org/bottelegram-secret/answerCallbackQuery",
                400,
                "Bad Request",
                None,
                None,
            ),
            "http_error",
        ),
        (urllib.error.URLError("stale callback transport failure"), "url_error"),
    ),
)
def test_poll_once_advances_offset_when_telegram_answer_fails(
    tmp_path: Path,
    answer_error: Exception,
    answer_error_name: str,
) -> None:
    state_path = tmp_path / "callback-state.json"
    update_payload = {"result": [{"update_id": 41, "callback_query": query()}]}
    with mock.patch.dict(
        os.environ, {"SKELETON_TG_BOT": "telegram-secret"}, clear=True
    ), mock.patch.object(
        poller.urllib.request,
        "urlopen",
        side_effect=(response(update_payload), answer_error),
    ):
        result = poller.poll_once(state_path=state_path)

    assert result["status"] == "polled"
    assert result["offset"] == 42
    assert json.loads(state_path.read_text(encoding="utf-8"))["offset"] == 42

    with mock.patch.dict(
        os.environ, {"SKELETON_TG_BOT": "telegram-secret"}, clear=True
    ), mock.patch.object(
        poller.urllib.request,
        "urlopen",
        side_effect=answer_error,
    ):
        answer_result = poller.handle_callback_query(query())

    assert answer_result["telegram_answer"] == "error"
    assert answer_result["telegram_answer_error"] == answer_error_name
    rendered = json.dumps(answer_result, sort_keys=True)
    assert "telegram-secret" not in rendered
    assert poller.TELEGRAM_API_BASE not in rendered


def test_poll_once_uses_configured_offset_state_path(tmp_path: Path) -> None:
    state_path = tmp_path / "configured" / "callback-state.json"
    state_path.parent.mkdir()
    state_path.write_text('{"offset":12}\n', encoding="utf-8")
    with mock.patch.dict(
        os.environ,
        {
            "SKELETON_TG_BOT": "telegram-secret",
            "SKELETON_TG_CALLBACK_STATE": str(state_path),
        },
        clear=True,
    ), mock.patch.object(
        poller.urllib.request,
        "urlopen",
        return_value=response({"result": []}),
    ) as urlopen:
        result = poller.poll_once()

    request = urlopen.call_args.args[0]
    assert "offset=12" in request.full_url
    assert result["offset"] == 12


def test_poll_once_duplicate_callback_skips_duplicate_audit_comment(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "callback-state.json"
    state_path.write_text(
        '{"handled_callback_ids":["callback-query-1"],"offset":41}\n',
        encoding="utf-8",
    )
    update_payload = {"result": [{"update_id": 41, "callback_query": query()}]}
    telegram_token = "telegram-secret-do-not-return"
    with mock.patch.dict(
        os.environ,
        {
            "GITHUB_TOKEN": "github-secret",
            "SKELETON_TG_BOT": telegram_token,
            poller.CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET,
        },
        clear=True,
    ), mock.patch.object(
        poller.urllib.request,
        "urlopen",
        side_effect=(response(update_payload), response()),
    ) as urlopen:
        result = poller.poll_once(state_path=state_path)

    requests = [call.args[0] for call in urlopen.call_args_list]
    assert result == {
        "status": "polled",
        "updates_seen": 1,
        "callbacks_seen": 1,
        "callbacks_handled": 1,
        "callbacks_duplicate": 1,
        "offset": 42,
    }
    assert len(requests) == 2
    assert all("api.github.com" not in request.full_url for request in requests)
    assert requests[-1].full_url.endswith("/bottelegram-secret-do-not-return/answerCallbackQuery")
    assert telegram_token not in json.dumps(result, sort_keys=True)
    assert poller.TELEGRAM_API_BASE not in json.dumps(result, sort_keys=True)


def test_poll_once_duplicate_signed_callback_data_skips_github_with_new_callback_id(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "callback-state.json"
    state_path.write_text(
        json.dumps(
            {
                "handled_callback_data": [CALLBACK_DATA],
                "handled_callback_ids": ["callback-query-1"],
                "offset": 41,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    update_payload = {
        "result": [
            {
                "update_id": 41,
                "callback_query": query(callback_id="callback-query-2"),
            }
        ]
    }
    with mock.patch.dict(
        os.environ,
        {
            "GITHUB_TOKEN": "github-secret",
            "SKELETON_TG_BOT": "telegram-secret",
            poller.CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET,
        },
        clear=True,
    ), mock.patch.object(
        poller.urllib.request,
        "urlopen",
        side_effect=(response(update_payload), response()),
    ) as urlopen:
        result = poller.poll_once(state_path=state_path)

    requests = [call.args[0] for call in urlopen.call_args_list]
    assert result["callbacks_duplicate"] == 1
    assert result["offset"] == 42
    assert len(requests) == 2
    assert all("api.github.com" not in request.full_url for request in requests)
    assert requests[-1].data == b"callback_query_id=callback-query-2"


def test_approve_callback_posts_audit_comment_when_pr_head_marker_matches() -> None:
    with mock.patch.dict(
        os.environ,
        {
            "GITHUB_TOKEN": "github-secret",
            poller.CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET,
        },
        clear=True,
    ), mock.patch.object(
        poller.urllib.request,
        "urlopen",
        side_effect=(
            response(github_pr_state()),
            response({"id": 88}),
            response({"number": 910}),
        ),
    ) as urlopen:
        result = poller.handle_callback_query(query())

    assert result["status"] == "comment_posted"
    assert result["comment_posted"] is True
    assert result["runner_merge_request"] == "requested"
    assert urlopen.call_count == 3
    fetch_request, post_request, request_issue = [
        call.args[0] for call in urlopen.call_args_list
    ]
    assert fetch_request.full_url.endswith("/repos/alanua/Skeleton/pulls/120")
    assert fetch_request.get_method() == "GET"
    assert post_request.full_url.endswith("/repos/alanua/Skeleton/issues/120/comments")
    assert post_request.get_method() == "POST"
    assert request_body(post_request) == {"body": result["comment"]}
    assert request_issue.full_url.endswith("/repos/alanua/Skeleton/issues")
    assert request_body(request_issue) == {
        "body": (
            "Mode: TELEGRAM_APPROVED_PR_MERGE\n"
            "Repository: alanua/Skeleton\n"
            "Pull Request: 120\n"
            f"Approved Head SHA: {HEAD_SHA}\n"
            "Merge Action: squash\n"
            "Approval Source: signed_telegram_callback\n"
            f"Callback Digest: {CALLBACK_DATA.rsplit(':', 1)[-1]}\n\n"
            "Runner must verify the PR, signed approval record, and head before squash merge."
        ),
        "labels": ["runner:ready"],
        "title": "Runner merge approved PR #120",
    }
    assert str(result["comment"]).startswith("Operator event record")
    assert "Verified approval record: signed_telegram_callback" in str(result["comment"])
    assert f"Verified head SHA: {HEAD_SHA}" in str(result["comment"])


def test_approve_callback_is_blocked_when_pr_head_marker_mismatches() -> None:
    with mock.patch.dict(
        os.environ,
        {
            "GITHUB_TOKEN": "github-secret",
            poller.CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET,
        },
        clear=True,
    ), mock.patch.object(
        poller.urllib.request,
        "urlopen",
        return_value=response(github_pr_state(head_sha="cafebabe" + "0" * 32)),
    ) as urlopen:
        result = poller.handle_callback_query(query())

    assert result["status"] == "blocked"
    assert result["comment_posted"] is False
    assert "head marker" in str(result["reason"])
    assert urlopen.call_count == 1


def test_reject_callback_posts_audit_comment_but_does_not_close_pr() -> None:
    reject = signed_callback_data("reject")
    with mock.patch.dict(
        os.environ,
        {
            "GITHUB_TOKEN": "github-secret",
            poller.CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET,
        },
        clear=True,
    ), mock.patch.object(
        poller.urllib.request,
        "urlopen",
        side_effect=(response(github_pr_state()), response({"id": 89})),
    ) as urlopen:
        result = poller.handle_callback_query(query(reject))

    urls = [call.args[0].full_url for call in urlopen.call_args_list]
    assert result["status"] == "comment_posted"
    assert "Action: telegram_reject" in str(result["comment"])
    assert result["runner_merge_request"] == "not_requested"
    assert urls == [
        "https://api.github.com/repos/alanua/Skeleton/pulls/120",
        "https://api.github.com/repos/alanua/Skeleton/issues/120/comments",
    ]


def test_details_callback_renders_audit_comment() -> None:
    details = signed_callback_data("details")
    parsed = poller.parse_callback_data(details)

    assert poller.render_audit_comment(parsed).startswith("Operator event record")
    assert "Action: telegram_details" in poller.render_audit_comment(parsed)


def test_details_callback_posts_audit_comment_only() -> None:
    details = signed_callback_data("details")
    with mock.patch.dict(
        os.environ,
        {
            "GITHUB_TOKEN": "github-secret",
            poller.CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET,
        },
        clear=True,
    ), mock.patch.object(
        poller.urllib.request,
        "urlopen",
        side_effect=(response(github_pr_state()), response({"id": 90})),
    ) as urlopen:
        result = poller.handle_callback_query(query(details))

    urls = [call.args[0].full_url for call in urlopen.call_args_list]
    assert result["status"] == "comment_posted"
    assert "Action: telegram_details" in str(result["comment"])
    assert result["runner_merge_request"] == "not_requested"
    assert urls == [
        "https://api.github.com/repos/alanua/Skeleton/pulls/120",
        "https://api.github.com/repos/alanua/Skeleton/issues/120/comments",
    ]


def test_details_callback_accepts_nosha_head_marker() -> None:
    details = signed_callback_data("details", head_marker="nosha")
    parsed = poller.parse_callback_data(details)

    assert parsed.head_marker == "nosha"
    assert "Action: telegram_details" in poller.render_audit_comment(parsed)


def test_approve_callback_with_nosha_head_marker_is_blocked() -> None:
    approve = signed_callback_data(head_marker="nosha")
    with mock.patch.dict(
        os.environ,
        {
            "GITHUB_TOKEN": "github-secret",
            poller.CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET,
        },
        clear=True,
    ), mock.patch.object(
        poller.urllib.request,
        "urlopen",
        return_value=response(github_pr_state()),
    ) as urlopen:
        result = poller.handle_callback_query(query(approve))

    assert result["status"] == "blocked"
    assert result["comment_posted"] is False
    assert "SHA head marker" in str(result["reason"])
    assert urlopen.call_count == 1


def test_reject_callback_with_nosha_head_marker_is_blocked() -> None:
    reject = signed_callback_data("reject", head_marker="nosha")
    with mock.patch.dict(
        os.environ,
        {
            "GITHUB_TOKEN": "github-secret",
            poller.CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET,
        },
        clear=True,
    ), mock.patch.object(
        poller.urllib.request,
        "urlopen",
        return_value=response(github_pr_state()),
    ) as urlopen:
        result = poller.handle_callback_query(query(reject))

    assert result["status"] == "blocked"
    assert result["comment_posted"] is False
    assert "SHA head marker" in str(result["reason"])
    assert urlopen.call_count == 1


def test_telegram_answer_callback_query_is_called_when_bot_token_exists() -> None:
    with mock.patch.dict(
        os.environ,
        {
            "SKELETON_TG_BOT": "telegram-secret",
            poller.CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET,
        },
        clear=True,
    ), mock.patch.object(
        poller.urllib.request,
        "urlopen",
        return_value=response(),
    ) as urlopen:
        result = poller.handle_callback_query(query())

    request = urlopen.call_args.args[0]
    assert result["status"] == "skipped"
    assert result["telegram_answer"] == "answered"
    assert request.full_url.endswith("/bottelegram-secret/answerCallbackQuery")
    assert b"callback_query_id=callback-query-1" == request.data


def test_no_token_appears_in_returned_result_or_comment() -> None:
    github_token = "github-token-never-return"
    telegram_token = "telegram-token-never-return"
    with mock.patch.dict(
        os.environ,
        {
            "GITHUB_TOKEN": github_token,
            "SKELETON_TG_BOT": telegram_token,
            poller.CALLBACK_HMAC_ENV: CALLBACK_HMAC_SECRET,
        },
        clear=True,
    ), mock.patch.object(
        poller.urllib.request,
        "urlopen",
        side_effect=(
            response(github_pr_state()),
            response({"id": 90}),
            response({"number": 911}),
            response(),
        ),
    ):
        result = poller.handle_callback_query(query())

    rendered = json.dumps(result, sort_keys=True)
    assert github_token not in rendered
    assert telegram_token not in rendered


def test_no_subprocess_usage() -> None:
    script = Path(poller.__file__).read_text(encoding="utf-8")
    with mock.patch.object(subprocess, "run") as run, mock.patch.object(
        poller.urllib.request, "urlopen"
    ) as urlopen:
        result = poller.handle_callback_query(query(), dry_run=True)

    assert "subprocess" not in script
    assert result["status"] == "dry_run"
    run.assert_not_called()
    urlopen.assert_not_called()


def test_stage_1_source_has_no_pr_action_endpoints() -> None:
    script = Path(poller.__file__).read_text(encoding="utf-8")

    assert "/merge" not in script
    assert '"/reject"' not in script
    assert "'/reject'" not in script
    assert "/close" not in script
    assert "/labels" not in script
    assert '"state":"closed"' not in script
    assert '"state": "closed"' not in script


def test_callback_poll_service_uses_environment_file_without_credentials() -> None:
    service = Path("scripts/skeleton-telegram-callback-poll.service").read_text(
        encoding="utf-8"
    )

    environment_lines = [line for line in service.splitlines() if line.startswith("Environment")]
    assert environment_lines == ["EnvironmentFile=-/etc/skeleton-runner.env"]
    assert "GITHUB_TOKEN=" not in service
    assert "SKELETON_TG_BOT=" not in service
