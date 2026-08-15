from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any, Final

from core.mail_operations import process_important_mail, public_mail_operation_receipt
from core.mail_provider import MailProvider, MailProviderAccount, MailProviderCursor
from core.mail_state import MailStateStore
from core.shared_dispatch import (
    PRIVACY_PUBLIC_SAFE,
    DispatchRoute,
    SharedDispatchRequest,
    SharedDispatcher,
)


MAIL_RUNTIME_RECEIPT_SCHEMA: Final = "skeleton.mail_runtime_receipt.v1"
MAIL_POLL_PACKET_SCHEMA: Final = "skeleton.mail_poll_packet.v1"
MAIL_POLL_ROUTE_TYPE: Final = "workflow"
MAIL_POLL_ROUTE_ID: Final = "mail.poll_provider"


@dataclass(frozen=True)
class MailRuntimeConfig:
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")


class MailRuntime:
    def __init__(
        self,
        *,
        state_store: MailStateStore,
        providers: Mapping[str, MailProvider],
        config: MailRuntimeConfig | None = None,
        clock: Any | None = None,
    ) -> None:
        self.state_store = state_store
        self.providers = dict(providers)
        self.config = config or MailRuntimeConfig()
        self._clock = clock or time.time

    def dispatch(self, request: SharedDispatchRequest) -> Mapping[str, Any]:
        return self.process_poll_packet(request.payload.get("task_packet", {}))

    def process_poll_packet(self, packet: Mapping[str, Any]) -> dict[str, Any]:
        now = int(self._clock())
        if not isinstance(packet, Mapping) or packet.get("schema") != MAIL_POLL_PACKET_SCHEMA:
            return _receipt("BLOCKED", "INVALID_MAIL_POLL_PACKET", now=now)
        account = MailProviderAccount.from_mapping(packet.get("account", {}))
        provider = self.providers.get(account.provider)
        if provider is None:
            return _receipt("BLOCKED", "MAIL_PROVIDER_NOT_CONFIGURED", account, now=now)

        self.state_store.initialize()
        cursor = MailProviderCursor(account.account_ref, self.state_store.get_cursor(account.account_ref))
        batch = provider.poll(account, cursor, max_messages=account.max_messages_per_poll)
        processed = ignored = operator = replayed = failed = 0
        message_receipts: list[dict[str, Any]] = []

        for envelope in batch.messages:
            message_hash = envelope.stable_message_hash()
            _, should_process = self.state_store.claim_message(
                message_hash=message_hash,
                account_ref=account.account_ref,
                now=now,
                max_attempts=self.config.max_attempts,
            )
            if not should_process:
                replayed += 1
                continue
            try:
                receipt = process_important_mail(envelope.__dict__, now=now)
            except Exception:
                failed += 1
                self.state_store.mark_message(
                    message_hash=message_hash,
                    status="failed",
                    reason="MAIL_PROCESSING_FAILED",
                    now=now,
                )
                continue

            public = public_mail_operation_receipt(receipt)
            message_receipts.append(public)
            if public.get("status") == "IGNORED":
                ignored += 1
                self.state_store.mark_message(
                    message_hash=message_hash,
                    status="ignored",
                    reason=str(public.get("reason") or "MAIL_IGNORED"),
                    now=now,
                )
            else:
                operator += 1
                self.state_store.mark_message(
                    message_hash=message_hash,
                    status="needs_operator",
                    reason=str(public.get("reason") or "MAIL_NEEDS_OPERATOR"),
                    now=now,
                )
            processed += 1

        self.state_store.update_cursor(
            account_ref=account.account_ref,
            provider=batch.provider,
            cursor_ref=batch.next_cursor_ref,
            now=now,
        )
        return {
            "schema": MAIL_RUNTIME_RECEIPT_SCHEMA,
            "status": "DONE" if failed == 0 else "NEEDS_OPERATOR",
            "accepted": failed == 0,
            "reason": "MAIL_POLL_PROCESSED" if failed == 0 else "MAIL_PROCESSING_PARTIAL_FAILURE",
            "account_ref": account.account_ref,
            "provider": account.provider,
            "polled": len(batch.messages),
            "processed": processed,
            "ignored": ignored,
            "needs_operator": operator,
            "replayed": replayed,
            "failed": failed,
            "message_receipts": message_receipts,
            "idempotency_key": _stable_hash(
                {"account_ref": account.account_ref, "cursor": cursor.cursor_ref, "now": now}
            )[:32],
            "public_safe": True,
            "private_payloads_included": False,
            "external_side_effects_executed": False,
        }


def build_mail_dispatcher(runtime: MailRuntime) -> SharedDispatcher:
    route = DispatchRoute(
        route_type=MAIL_POLL_ROUTE_TYPE,
        route_id=MAIL_POLL_ROUTE_ID,
        required_capabilities=frozenset({"mail:poll"}),
        handler=runtime.dispatch,
    )
    return SharedDispatcher({(MAIL_POLL_ROUTE_TYPE, MAIL_POLL_ROUTE_ID): route})


def build_mail_poll_payload(account: MailProviderAccount) -> dict[str, Any]:
    return {
        "privacy_boundary": PRIVACY_PUBLIC_SAFE,
        "bounded": True,
        "approved_capabilities": ["mail:poll"],
        "requested_capabilities": ["mail:poll"],
        "task_packet": {
            "schema": MAIL_POLL_PACKET_SCHEMA,
            "account": account.to_mapping(),
        },
    }


def _receipt(
    status: str,
    reason: str,
    account: MailProviderAccount | None = None,
    *,
    now: int,
) -> dict[str, Any]:
    return {
        "schema": MAIL_RUNTIME_RECEIPT_SCHEMA,
        "status": status,
        "accepted": False,
        "reason": reason,
        "account_ref": None if account is None else account.account_ref,
        "provider": None if account is None else account.provider,
        "polled": 0,
        "processed": 0,
        "ignored": 0,
        "needs_operator": 0,
        "replayed": 0,
        "failed": 0,
        "recorded_at": now,
        "public_safe": True,
        "private_payloads_included": False,
        "external_side_effects_executed": False,
    }


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
