from __future__ import annotations

from collections.abc import Mapping, Sequence
from email.utils import parsedate_to_datetime
import json
import time
import urllib.parse
import urllib.request
from typing import Any

from core.mail_provider import (
    MailCleanupAction,
    MailProviderAccount,
    MailProviderError,
    MailChange,
    ProviderMailMessage,
    ProviderScanResult,
    decode_base64url_text,
)


GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"


class GmailMailProvider:
    provider_name = "gmail"

    def __init__(self, *, api_base: str = GMAIL_API_BASE, opener: Any | None = None) -> None:
        self.api_base = api_base.rstrip("/")
        self._opener = opener or urllib.request.urlopen

    def scan(self, account: MailProviderAccount, *, cursor: str | None, limit: int) -> ProviderScanResult:
        token = account.require_secret()
        bounded_limit = max(1, min(limit, 100))
        if cursor:
            url = f"{self.api_base}/users/me/history?{urllib.parse.urlencode({'startHistoryId': cursor, 'historyTypes': 'messageAdded', 'maxResults': bounded_limit})}"
            payload = self._json_request(url, token)
            changes: list[MailChange] = []
            for history in payload.get("history", []) if isinstance(payload.get("history"), list) else []:
                history_id = str(history.get("id") or "")
                for item in history.get("messagesAdded", []) if isinstance(history.get("messagesAdded"), list) else []:
                    message = item.get("message") if isinstance(item, Mapping) else None
                    if isinstance(message, Mapping) and isinstance(message.get("id"), str):
                        changes.append(MailChange(message["id"], message.get("threadId"), int(time.time()), history_id))
            return ProviderScanResult(str(payload.get("historyId") or cursor), tuple(changes))

        query = "in:inbox newer_than:30d"
        url = f"{self.api_base}/users/me/messages?{urllib.parse.urlencode({'q': query, 'maxResults': bounded_limit})}"
        payload = self._json_request(url, token)
        changes = tuple(
            MailChange(str(item["id"]), item.get("threadId"), int(time.time()))
            for item in payload.get("messages", [])
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        )
        profile = self._json_request(f"{self.api_base}/users/me/profile", token)
        return ProviderScanResult(str(profile.get("historyId") or "0"), changes)

    def fetch_message(
        self, account: MailProviderAccount, *, provider_message_ref: str
    ) -> ProviderMailMessage:
        token = account.require_secret()
        url = f"{self.api_base}/users/me/messages/{urllib.parse.quote(provider_message_ref)}?format=metadata"
        payload = self._json_request(url, token)
        headers = _headers(payload)
        received_at = _received_epoch(headers.get("date"), payload.get("internalDate"))
        return ProviderMailMessage(
            provider=self.provider_name,
            provider_message_ref=str(payload.get("id") or provider_message_ref),
            provider_thread_ref=payload.get("threadId") if isinstance(payload.get("threadId"), str) else None,
            received_at=received_at,
            subject=headers.get("subject", ""),
            body_preview=str(payload.get("snippet") or ""),
            sender=headers.get("from"),
            labels=tuple(str(item) for item in payload.get("labelIds", []) if isinstance(item, str)),
            attachments=(),
            headers=headers,
        )

    def apply_cleanup(
        self, account: MailProviderAccount, *, actions: Sequence[MailCleanupAction]
    ) -> Mapping[str, Any]:
        token = account.require_secret()
        changed = 0
        for action in actions:
            if action.action != "label" or not action.label:
                continue
            url = f"{self.api_base}/users/me/messages/{urllib.parse.quote(action.provider_message_ref)}/modify"
            body = json.dumps({"addLabelIds": [action.label], "removeLabelIds": []}).encode("utf-8")
            self._json_request(url, token, data=body)
            changed += 1
        return {
            "status": "DONE",
            "reason": "GMAIL_CLEANUP_APPLIED",
            "changed": changed,
            "public_safe": True,
            "private_payloads_included": False,
        }

    def send_message(self, *_args: Any, **_kwargs: Any) -> Mapping[str, Any]:
        raise MailProviderError("SEND_DISABLED", "external Gmail send is disabled")

    def _json_request(self, url: str, token: str, *, data: bytes | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            method="POST" if data is not None else "GET",
        )
        try:
            with self._opener(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except MailProviderError:
            raise
        except Exception as exc:
            raise MailProviderError("PROVIDER_REQUEST_FAILED", "provider request failed") from exc
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise MailProviderError("PROVIDER_RESPONSE_INVALID", "provider response is invalid") from exc
        if not isinstance(parsed, dict):
            raise MailProviderError("PROVIDER_RESPONSE_INVALID", "provider response must be an object")
        return parsed


def _headers(payload: Mapping[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    raw_headers = payload.get("payload", {}).get("headers", []) if isinstance(payload.get("payload"), Mapping) else []
    for item in raw_headers if isinstance(raw_headers, list) else []:
        if isinstance(item, Mapping) and isinstance(item.get("name"), str) and isinstance(item.get("value"), str):
            headers[item["name"].lower()] = item["value"]
    return headers


def _received_epoch(date_header: str | None, internal_date: Any) -> int:
    if isinstance(date_header, str):
        try:
            return int(parsedate_to_datetime(date_header).timestamp())
        except Exception:
            pass
    try:
        return int(int(internal_date) / 1000)
    except Exception:
        return int(time.time())
