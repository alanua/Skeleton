from __future__ import annotations

from collections.abc import Mapping
import base64
from email.utils import parseaddr
import re
from typing import Any

from adapters.gmail_oauth_client import GmailOAuthBundle, GmailOAuthClient, GmailOAuthError, Transport
from core.mail_operations import MailEnvelope, MailOperationError
from core.mail_provider import MailProviderAccount, MailProviderBatch, MailProviderCursor
from integrations.credential_runtime import (
    RegisteredCredentialRuntimeError,
    consume_registered_material_credential,
)


class GmailMailProvider:
    provider = "gmail"

    def __init__(self, client: Any) -> None:
        self._client = client

    def poll(
        self,
        account: MailProviderAccount,
        cursor: MailProviderCursor,
        *,
        max_messages: int,
    ) -> MailProviderBatch:
        if account.provider != self.provider:
            raise MailOperationError("MAIL_PROVIDER_MISMATCH", "account provider is not gmail")
        if cursor.account_ref != account.account_ref:
            raise MailOperationError("MAIL_CURSOR_ACCOUNT_MISMATCH", "cursor account mismatch")

        rows = self._list_messages(account.query, max_messages=max_messages)
        envelopes = tuple(self._message_to_envelope(row) for row in rows)
        next_cursor = envelopes[-1].provider_message_ref if envelopes else cursor.cursor_ref
        return MailProviderBatch(
            account_ref=account.account_ref,
            provider=self.provider,
            messages=envelopes,
            next_cursor_ref=next_cursor,
        )

    def _list_messages(self, query: str, *, max_messages: int) -> tuple[Mapping[str, Any], ...]:
        if hasattr(self._client, "list_messages"):
            return tuple(self._client.list_messages(query=query, max_results=max_messages))
        raise MailOperationError("GMAIL_CLIENT_CONTRACT_INVALID", "gmail client is not read-only capable")

    @staticmethod
    def _message_to_envelope(value: Mapping[str, Any]) -> MailEnvelope:
        provider_message_ref = str(value.get("id") or value.get("provider_message_ref") or "")
        thread_ref = value.get("threadId") or value.get("thread_ref")
        internal_date = value.get("internalDate") or value.get("received_at") or 0
        received_at = int(int(internal_date) / 1000) if str(internal_date).isdigit() else 0
        headers = _headers(value)
        subject = headers.get("subject") or str(value.get("subject_hint") or "(no subject)")
        sender = headers.get("from") or value.get("sender_ref")
        snippet = str(value.get("snippet") or value.get("body_preview") or subject)
        label_ids = value.get("labelIds", ())
        importance = "high" if isinstance(label_ids, list) and "IMPORTANT" in label_ids else None
        payload_text = _payload_preview(value.get("payload"))
        body_preview = payload_text or snippet or subject
        return MailEnvelope.from_mapping(
            {
                "provider": "gmail",
                "provider_message_ref": provider_message_ref,
                "received_at": received_at,
                "subject_hint": subject,
                "body_preview": body_preview,
                "importance_hint": importance,
                "deadline_hint": snippet,
                "sender_ref": _opaque_sender_ref(sender),
                "thread_ref": None if thread_ref is None else str(thread_ref),
                "security_metadata": _security_metadata(headers),
            }
        )


def build_registered_gmail_provider(
    *,
    account_ref: str,
    authority_environment: Mapping[str, str],
    transport: Transport | None = None,
) -> GmailMailProvider:
    """Build one Gmail provider from a code-owned registered OAuth binding.

    No provider/reference/path/host is accepted from the caller. Secret material is
    consumed in memory and never returned by the credential runtime.
    """

    holder: dict[str, GmailMailProvider] = {}

    def consume(raw: str) -> None:
        bundle = GmailOAuthBundle.from_json(raw)
        holder["provider"] = GmailMailProvider(GmailOAuthClient(bundle, transport=transport))

    try:
        receipt = consume_registered_material_credential(
            service_id="mail-gmail",
            alias=account_ref,
            action_id="use-gmail-readonly-oauth",
            consumer=consume,
            authority_environment=authority_environment,
        )
    except RegisteredCredentialRuntimeError as exc:
        raise MailOperationError("GMAIL_CREDENTIAL_UNAVAILABLE", "registered Gmail credential unavailable") from exc
    except GmailOAuthError as exc:
        raise MailOperationError(exc.reason_code, "Gmail OAuth bundle rejected") from exc
    result = receipt.get("result")
    if not isinstance(result, Mapping) or result.get("status") != "USED" or "provider" not in holder:
        raise MailOperationError("GMAIL_CREDENTIAL_UNAVAILABLE", "registered Gmail credential unavailable")
    return holder["provider"]


def _headers(value: Mapping[str, Any]) -> dict[str, str]:
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        return {}
    headers = payload.get("headers")
    if not isinstance(headers, list):
        return {}
    output: dict[str, str] = {}
    for item in headers:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").lower()
        if name in {"subject", "from", "reply-to", "authentication-results"}:
            output[name] = str(item.get("value") or "")
    return output


def _security_metadata(headers: Mapping[str, str]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    sender_domain = _address_domain(headers.get("from"))
    reply_to_domain = _address_domain(headers.get("reply-to"))
    if sender_domain is not None:
        metadata["sender_domain"] = sender_domain
    if reply_to_domain is not None:
        metadata["reply_to_domain"] = reply_to_domain
    auth = _authentication_status(headers.get("authentication-results", ""))
    if auth:
        metadata["authentication"] = auth
    return metadata


def _address_domain(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    _name, address = parseaddr(value)
    candidate = address.rsplit("@", 1)[-1].strip().lower() if "@" in address else ""
    return candidate if re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}\.[a-z]{2,63}", candidate) else None


def _authentication_status(value: str) -> dict[str, str]:
    lowered = value.lower()
    output: dict[str, str] = {}
    for key in ("spf", "dkim", "dmarc"):
        match = re.search(rf"\b{key}\s*=\s*(pass|fail|neutral|softfail|none|temperror|permerror)\b", lowered)
        if match is not None:
            output[key] = match.group(1).upper()
    return output


def _payload_preview(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    body = payload.get("body")
    if isinstance(body, Mapping) and isinstance(body.get("data"), str):
        try:
            decoded = base64.urlsafe_b64decode(body["data"] + "==").decode("utf-8", "replace")
        except (ValueError, UnicodeDecodeError):
            decoded = ""
        preview = " ".join(decoded.split())
        return preview[:4096] if preview else None
    for part in payload.get("parts", ()) if isinstance(payload.get("parts"), list) else ():
        preview = _payload_preview(part)
        if preview:
            return preview
    return None


def _opaque_sender_ref(sender: Any) -> str | None:
    if not isinstance(sender, str) or not sender:
        return None
    import hashlib

    return "sender:" + hashlib.sha256(sender.lower().encode("utf-8")).hexdigest()[:24]
