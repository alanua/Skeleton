from __future__ import annotations

import json
import subprocess
from unittest import mock

import pytest

from scripts import telegram_callback_poller as poller


TOKEN = "github-token-placeholder"
BOT_TOKEN = "telegram-bot-placeholder"
HEAD_SHA = "a1b2c3d4" + "0" * 32
CALLBACK_DATA = "tpr1:approve:p123:a1b2c3d4:0123456789ab"


def _callback(data: str = CALLBACK_DATA) -> dict[str, str]:
    return {"id": "telegram-query-1", "data": data}


def _response(payload: object = None) -> mock.MagicMock:
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps({} if payload is None else payload).encode("utf-8")
    return response


def _request_payload(request: object) -> dict[str, object]:
    return json.loads(request.data.decode("utf-8"))


def test_parses_valid_callback_data() -> None:
    parsed = poller.parse_callback_data(CALLBACK_DATA)

    assert parsed == poller.CallbackData(
        action="approve",
        pr_number=123,
        head_marker="a1b2c3d4",
        digest="0123456789ab",
    )


@pytest.mark.parametrize(
    "callback_data",
    (
        "other:approve:p123:a1b2c3d4:0123456789ab",
        "tpr1:merge:p123:a1b2c3d4:0123456789ab",
        "tpr1:approve:p0:a1b2c3d4:0123456789ab",
        "tpr1:approve:p123:not-sha8:0123456789ab",
        "tpr1:approve:p123:a1b2c3d4:not-digest12",
    ),
)
def test_rejects_malformed_prefix_action_pr_sha_and_digest(callback_data: str) -> None:
    with pytest.raises(ValueError, match="bounded tpr1"):
        poller.parse_callback_data(callback_data)


def test_dry_run_does_not_call_urllib() -> None:
    with mock.patch.object(poller.urllib.request, "urlopen") as urlopen:
        result = poller.handle_callback_query(
            _callback(),
            dry_run=True,
            environ={"GITHUB_TOKEN": TOKEN, "SKELETON_TG_BOT": BOT_TOKEN},
        )

    assert result["status"] == "dry_run"
    assert result["comment_status"] == "dry_run"
    urlopen.assert_not_called()


def test_missing_github_token_returns_skipped_no_post_result() -> None:
    with mock.patch.object(poller.urllib.request, "urlopen") as urlopen:
        result = poller.handle_callback_query(_callback(), environ={})

    assert result["status"] == "skipped"
    assert result["comment_status"] == "skipped_no_github_token"
    urlopen.assert_not_called()


def test_approve_callback_posts_audit_comment_when_pr_head_marker_matches() -> None:
    responses = [_response({"number": 123, "head": {"sha": HEAD_SHA}}), _response()]
    with mock.patch.object(
        poller.urllib.request, "urlopen", side_effect=responses
    ) as urlopen:
        result = poller.handle_callback_query(_callback(), environ={"GITHUB_TOKEN": TOKEN})

    assert result["status"] == "posted"
    assert urlopen.call_count == 2
    post_request = urlopen.call_args_list[1].args[0]
    assert post_request.full_url.endswith("/repos/alanua/Skeleton/issues/123/comments")
    assert _request_payload(post_request)["body"].startswith("Operator event record")


def test_approve_callback_is_blocked_when_pr_head_marker_mismatches() -> None:
    with mock.patch.object(
        poller.urllib.request,
        "urlopen",
        return_value=_response({"number": 123, "head": {"sha": "f" * 40}}),
    ) as urlopen:
        result = poller.handle_callback_query(_callback(), environ={"GITHUB_TOKEN": TOKEN})

    assert result["status"] == "blocked"
    assert result["comment_status"] == "not_posted"
    assert "head marker" in str(result["reason"])
    urlopen.assert_called_once()


def test_reject_callback_posts_audit_comment_but_does_not_close_pr() -> None:
    responses = [_response({"number": 123, "head": {"sha": HEAD_SHA}}), _response()]
    reject_data = "tpr1:reject:p123:a1b2c3d4:0123456789ab"
    with mock.patch.object(
        poller.urllib.request, "urlopen", side_effect=responses
    ) as urlopen:
        result = poller.handle_callback_query(
            _callback(reject_data),
            environ={"GITHUB_TOKEN": TOKEN},
        )

    assert result["status"] == "posted"
    assert all("/pulls/123" in call.args[0].full_url or "/comments" in call.args[0].full_url for call in urlopen.call_args_list)
    assert all(call.args[0].get_method() != "PATCH" for call in urlopen.call_args_list)


def test_details_callback_renders_audit_comment() -> None:
    result = poller.handle_callback_query(
        _callback("tpr1:details:p123:a1b2c3d4:0123456789ab"),
        dry_run=True,
        environ={},
    )

    assert str(result["comment"]).startswith("Operator event record")
    assert "Action: telegram_details" in str(result["comment"])


def test_telegram_answer_callback_query_is_called_when_bot_token_exists() -> None:
    responses = [
        _response({"number": 123, "head": {"sha": HEAD_SHA}}),
        _response(),
        _response(),
    ]
    with mock.patch.object(
        poller.urllib.request, "urlopen", side_effect=responses
    ) as urlopen:
        result = poller.handle_callback_query(
            _callback(),
            environ={"GITHUB_TOKEN": TOKEN, "SKELETON_TG_BOT": BOT_TOKEN},
        )

    telegram_request = urlopen.call_args_list[2].args[0]
    assert result["telegram_answer_status"] == "answered"
    assert telegram_request.full_url.endswith("/answerCallbackQuery")
    assert _request_payload(telegram_request) == {"callback_query_id": "telegram-query-1"}


def test_no_token_appears_in_returned_result_or_comment() -> None:
    result = poller.handle_callback_query(
        _callback(),
        dry_run=True,
        environ={"GITHUB_TOKEN": TOKEN, "SKELETON_TG_BOT": BOT_TOKEN},
    )
    rendered = json.dumps(result, sort_keys=True)

    assert TOKEN not in rendered
    assert BOT_TOKEN not in rendered
    assert TOKEN not in str(result["comment"])
    assert BOT_TOKEN not in str(result["comment"])


def test_no_subprocess_usage() -> None:
    with mock.patch.object(subprocess, "run") as run:
        result = poller.handle_callback_query(_callback(), dry_run=True, environ={})

    assert result["status"] == "dry_run"
    run.assert_not_called()
