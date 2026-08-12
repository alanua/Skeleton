from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from scripts import telegram_callback_poller as poller


SCAN_SECRET = "scan-action-test-secret"
SCAN_TOKEN = "0123456789abcdef"


def scan_callback_data(
    *,
    token: str = SCAN_TOKEN,
    secret: str = SCAN_SECRET,
) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        f"sdoc1:e:{token}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()[:12]
    return f"sdoc1:e:{token}:{digest}"


def scan_query(callback_data: object = None) -> dict[str, object]:
    return {
        "id": "scan-callback-1",
        "data": scan_callback_data() if callback_data is None else callback_data,
        "message": {"chat": {"id": 12345}},
    }


def write_issued(root: Path, *, token: str = SCAN_TOKEN, expires_at: float = 9_999_999_999.0) -> None:
    issued = root / "issued" / f"{token}.json"
    issued.parent.mkdir(parents=True)
    issued.write_text(
        json.dumps(
            {
                "schema": "skeleton.scan_action.issue.v1",
                "token": token,
                "document": "scan.pdf",
                "expires_at": expires_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "callback_data",
    (
        "sdoc1:e:0123456789abcdef:0123456789ab",
        scan_callback_data(),
    ),
)
def test_scan_callback_parser_accepts_only_bounded_approved_format(callback_data: str) -> None:
    assert poller._SCAN_CALLBACK_RE.fullmatch(callback_data) is not None
    assert len(callback_data.encode("utf-8")) <= poller.TELEGRAM_CALLBACK_DATA_LIMIT


@pytest.mark.parametrize(
    "callback_data",
    (
        "sdoc1:e:0123456789abcde:0123456789ab",
        "sdoc1:e:0123456789abcdef0:0123456789ab",
        "sdoc1:e:0123456789abcdeg:0123456789ab",
        "sdoc1:e:0123456789abcdef:0123456789a",
        "sdoc1:e:0123456789abcdef:0123456789abc",
        "sdoc1:e:0123456789abcdef:0123456789ag",
        "sdoc1:x:0123456789abcdef:0123456789ab",
        "sdoc1:e:0123456789abcdef:0123456789ab:extra",
    ),
)
def test_scan_callback_parser_rejects_malformed_or_unbounded_values(callback_data: str) -> None:
    assert poller._SCAN_CALLBACK_RE.fullmatch(callback_data) is None
    with mock.patch.object(poller, "_scan_action_secret") as secret:
        result = poller.handle_callback_query(scan_query(callback_data), dry_run=True)

    secret.assert_not_called()
    assert result["status"] == "blocked"
    assert result["github"] == "not_called"
    assert "secret" not in str(result).lower()


def test_missing_scan_action_secret_fails_closed_without_leaking_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "scan-actions"
    monkeypatch.setattr(poller, "SCAN_ACTION_ROOT", root)
    monkeypatch.setattr(
        poller,
        "_scan_action_secret",
        mock.Mock(side_effect=RuntimeError("scan action credential unavailable")),
    )
    write_issued(root)

    with pytest.raises(RuntimeError) as excinfo:
        poller.handle_callback_query(scan_query(), dry_run=False)

    assert "credential unavailable" in str(excinfo.value)
    assert SCAN_SECRET not in str(excinfo.value)
    assert not (root / "pending" / f"{SCAN_TOKEN}.json").exists()
    assert not list(root.glob("**/.*.tmp"))


def test_invalid_scan_action_secret_fails_closed_without_leaking_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "scan-actions"
    monkeypatch.setattr(poller, "SCAN_ACTION_ROOT", root)
    monkeypatch.setattr(poller, "_scan_action_secret", lambda: "different-secret")
    write_issued(root)

    result = poller.handle_callback_query(scan_query(), dry_run=False)

    assert result["status"] == "blocked"
    assert result["github"] == "not_called"
    assert result["comment"] is None
    assert SCAN_SECRET not in str(result)
    assert "different-secret" not in str(result)
    assert not (root / "pending" / f"{SCAN_TOKEN}.json").exists()
    assert not list(root.glob("**/.*.tmp"))


def test_scan_action_state_write_is_atomic_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "scan-actions"
    monkeypatch.setattr(poller, "SCAN_ACTION_ROOT", root)
    monkeypatch.setattr(poller, "_scan_action_secret", lambda: SCAN_SECRET)
    monkeypatch.delenv("SKELETON_TG_CHAT", raising=False)
    monkeypatch.delenv("SKELETON_TG_BOT", raising=False)
    write_issued(root)

    result = poller.handle_callback_query(scan_query(), dry_run=False)

    pending = root / "pending" / f"{SCAN_TOKEN}.json"
    assert result["status"] == "queued"
    assert result["github"] == "not_called"
    assert result["telegram_answer"] == "skipped"
    assert pending.exists()
    assert not list(root.glob("**/.*.tmp"))
    assert oct(pending.stat().st_mode & 0o777) == "0o600"

    payload = json.loads(pending.read_text(encoding="utf-8"))
    assert payload["schema"] == "skeleton.scan_action.request.v1"
    assert payload["status"] == "pending"
    assert payload["attempts"] == 0
    assert payload["next_attempt"] == 0
    assert payload["chat_id"] == 12345
    assert set(payload) == {
        "attempts",
        "chat_id",
        "document",
        "expires_at",
        "next_attempt",
        "requested_at",
        "schema",
        "status",
        "token",
    }


def test_unrelated_pr_callback_behavior_does_not_route_to_scan_handler() -> None:
    with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "github-secret"}, clear=True), mock.patch.object(
        poller, "_handle_scan_callback"
    ) as scan_handler, mock.patch.object(poller.urllib.request, "urlopen") as urlopen:
        result = poller.handle_callback_query(
            {"id": "callback-query-1", "data": "tpr1:merge:p120:deadbeef:0123456789ab"}
        )

    scan_handler.assert_not_called()
    urlopen.assert_not_called()
    assert result["status"] == "blocked"
    assert result["github"] == "not_called"
