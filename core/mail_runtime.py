from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Final

from core.domain_event_graph import DOMAIN_EVENT_ENVELOPE_SCHEMA, DomainEventGraph
from core.mail_operations import normalize_correspondence, process_important_mail
from core.mail_provider import MailProvider, MailProviderAccount, MailProviderError
from core.mail_state import MailStateStore
from core.scheduler_store import SchedulerStore
from integrations.mail_scheduler import register_mail_deadline_checkpoint
from integrations.mail_telegram import build_telegram_operator_packet


MAIL_RUNTIME_RECEIPT_SCHEMA: Final = "skeleton.mail_runtime.receipt.v1"
PRIVATE_BOUNDARY: Final = "PRIVATE_EMAIL_CONTENT_LOCAL_ONLY"
PUBLIC_BOUNDARY: Final = "PUBLIC_SAFE_AGGREGATES_ONLY"

_GITHUB_TECH_RE = re.compile(r"\b(github|pull request|workflow run|check run|ci)\b", re.I)
_INVOICE_RE = re.compile(r"\b(invoice|rechnung|payment|receipt|zahlung)\b", re.I)
_DOCUMENT_RE = re.compile(r"\b(document|contract|anlage|attachment|pdf)\b", re.I)


@dataclass(frozen=True)
class MailRuntimeConfig:
    accounts: tuple[MailProviderAccount, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MailRuntimeConfig":
        if not isinstance(value, Mapping):
            raise MailRuntimeError("INVALID_CONFIG", "config must be an object")
        accounts = value.get("accounts")
        if not isinstance(accounts, Sequence) or isinstance(accounts, (str, bytes)) or not accounts:
            raise MailRuntimeError("INVALID_CONFIG", "accounts must be a non-empty list")
        return cls(accounts=tuple(MailProviderAccount.from_mapping(item) for item in accounts))


class MailRuntimeError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class MailRuntime:
    def __init__(
        self,
        *,
        state: MailStateStore,
        scheduler: SchedulerStore,
        providers: Mapping[str, MailProvider],
        domain_events: DomainEventGraph | None = None,
    ) -> None:
        self.state = state
        self.scheduler = scheduler
        self.providers = dict(providers)
        self.domain_events = domain_events or DomainEventGraph()

    def poll_once(self, config: MailRuntimeConfig, *, now: int) -> dict[str, Any]:
        _timestamp(now, "now")
        self.state.initialize()
        self.scheduler.initialize()
        totals = {
            "accounts": 0,
            "auth_required": 0,
            "polled_messages": 0,
            "new_messages": 0,
            "duplicate_messages": 0,
            "operator_packets": 0,
            "scheduler_checkpoints": 0,
            "private_routes": 0,
            "github_cleanup_candidates": 0,
            "github_cleanup_authorized": 0,
        }
        account_receipts: list[dict[str, Any]] = []
        for account in config.accounts:
            totals["accounts"] += 1
            provider = self.providers.get(account.provider)
            if provider is None:
                account_receipts.append(_account_receipt(account, "BLOCKED", "PROVIDER_NOT_CONFIGURED"))
                continue
            if account.secret_reference is None:
                totals["auth_required"] += 1
                account_receipts.append(_account_receipt(account, "AUTH_REQUIRED", "AUTH_REQUIRED"))
                continue
            cursor = self.state.get_cursor(account.account_ref)
            try:
                result = provider.poll(account, cursor=cursor, now=now)
            except MailProviderError as exc:
                reason = exc.reason_code
                status = "AUTH_REQUIRED" if reason == "AUTH_REQUIRED" else "BLOCKED"
                if status == "AUTH_REQUIRED":
                    totals["auth_required"] += 1
                account_receipts.append(_account_receipt(account, status, reason))
                continue
            if result.status == "AUTH_REQUIRED":
                totals["auth_required"] += 1
                account_receipts.append(_account_receipt(account, "AUTH_REQUIRED", result.reason))
                continue
            polled = 0
            new_count = 0
            duplicate_count = 0
            for message in result.messages:
                polled += 1
                outcome = self._ingest_message(account, message, now=now)
                new_count += int(outcome["new"])
                duplicate_count += int(outcome["duplicate"])
                totals["operator_packets"] += int(outcome["operator_packet_created"])
                totals["scheduler_checkpoints"] += int(outcome["scheduler_checkpoint_created"])
                totals["private_routes"] += int(outcome["private_route_created"])
                totals["github_cleanup_candidates"] += int(outcome["github_cleanup_candidate"])
                totals["github_cleanup_authorized"] += int(outcome["github_cleanup_authorized"])
            totals["polled_messages"] += polled
            totals["new_messages"] += new_count
            totals["duplicate_messages"] += duplicate_count
            if result.next_cursor is not None:
                self.state.set_cursor(
                    account.account_ref, account.provider, result.next_cursor, now=now
                )
            account_receipts.append(
                {
                    **_account_receipt(account, "OK", result.reason),
                    "message_count": polled,
                    "new_messages": new_count,
                    "duplicate_messages": duplicate_count,
                    "cursor_advanced": result.next_cursor is not None,
                }
            )
        return {
            "schema": MAIL_RUNTIME_RECEIPT_SCHEMA,
            "status": "AUTH_REQUIRED" if totals["auth_required"] and totals["polled_messages"] == 0 else "OK",
            "reason": "AUTH_REQUIRED" if totals["auth_required"] and totals["polled_messages"] == 0 else "POLL_COMPLETE",
            "privacy_boundary": PUBLIC_BOUNDARY,
            "private_intake_boundary": PRIVATE_BOUNDARY,
            "aggregate_counts": totals,
            "accounts": account_receipts,
            "state_counts": self.state.aggregate_counts(),
            "health": {"ready": True, "canary": "aggregate_receipt_only"},
            "public_safe": True,
            "private_payloads_included": False,
            "external_side_effects_executed": False,
            "external_email_send_enabled": False,
            "telegram_send_executed": False,
            "github_cleanup_executed": False,
        }

    def _ingest_message(self, account: MailProviderAccount, message: Any, *, now: int) -> dict[str, bool]:
        envelope = message.to_envelope(account.account_ref)
        normalized = normalize_correspondence(envelope)
        if self.state.has_message(normalized.message_hash):
            return _message_outcome(duplicate=True)

        operation_receipt = process_important_mail(envelope, now=now)
        inserted = self.state.record_message(
            message_hash=normalized.message_hash,
            account_ref=account.account_ref,
            provider=account.provider,
            provider_message_ref=envelope["provider_message_ref"],
            case_ref=normalized.case_ref,
            correspondence_ref=normalized.correspondence_ref,
            important=normalized.important,
            deadline_at=normalized.deadline_at,
            processed_at=now,
            receipt=operation_receipt,
        )
        if not inserted:
            return _message_outcome(duplicate=True)

        self._record_domain_event(envelope, normalized, now=now)
        scheduler_created = False
        operator_created = False
        route_created = False
        checkpoint = operation_receipt.get("scheduler_checkpoint")
        if isinstance(checkpoint, Mapping):
            scheduler_created = register_mail_deadline_checkpoint(
                self.scheduler, checkpoint, message_hash=normalized.message_hash, now=now
            )
            self.state.record_deadline(
                schedule_id=str(checkpoint["schedule_id"]),
                message_hash=normalized.message_hash,
                now=now,
            )
        packet = operation_receipt.get("operator_packet")
        if isinstance(packet, Mapping):
            telegram_packet = build_telegram_operator_packet(packet)
            packet_ref = _stable_ref("mail_operator_packet", normalized.message_hash)
            operator_created = self.state.record_operator_packet(
                packet_ref=packet_ref,
                message_hash=normalized.message_hash,
                created_at=now,
                packet=telegram_packet,
            )
        route = _private_route_for_message(message, normalized.message_hash)
        if route is not None:
            route_created = self.state.record_private_route(
                route_ref=route["route_ref"],
                message_hash=normalized.message_hash,
                route_type=route["route_type"],
                target_ref=route["target_ref"],
                now=now,
            )
        cleanup = _github_cleanup_authority(message)
        return _message_outcome(
            new=True,
            operator_packet_created=operator_created,
            scheduler_checkpoint_created=scheduler_created,
            private_route_created=route_created,
            github_cleanup_candidate=cleanup["candidate"],
            github_cleanup_authorized=cleanup["authorized"],
        )

    def _record_domain_event(self, envelope: Mapping[str, Any], normalized: Any, *, now: int) -> None:
        self.domain_events.ingest(
            {
                "schema": DOMAIN_EVENT_ENVELOPE_SCHEMA,
                "domain": "mail",
                "event_type": "mail_correspondence_ingested",
                "source_ref": normalized.correspondence_ref,
                "observed_at": now,
                "idempotency_key": f"mail-{normalized.message_hash}",
                "refs": [
                    {"ref_type": "mail", "ref_id": normalized.correspondence_ref},
                    {"ref_type": "case", "ref_id": normalized.case_ref},
                ],
                "provenance_refs": [
                    {"ref": f"mail:{normalized.message_hash[:24]}", "kind": "mail_message_hash"}
                ],
                "confidence": 1.0,
                "inferred": False,
            }
        )


def health_canary(state: MailStateStore) -> dict[str, Any]:
    state.initialize()
    return {
        "schema": MAIL_RUNTIME_RECEIPT_SCHEMA,
        "status": "READY",
        "reason": "HEALTH_CANARY",
        "privacy_boundary": PUBLIC_BOUNDARY,
        "aggregate_counts": state.aggregate_counts(),
        "public_safe": True,
        "private_payloads_included": False,
        "external_side_effects_executed": False,
    }


def _private_route_for_message(message: Any, message_hash: str) -> dict[str, str] | None:
    labels = " ".join(getattr(message, "labels", ()) or ())
    text = f"{getattr(message, 'subject', '')} {getattr(message, 'body_preview', '')} {labels}"
    if _INVOICE_RE.search(text):
        return {
            "route_ref": _stable_ref("route_invoice", message_hash),
            "route_type": "invoice_payment_evidence",
            "target_ref": f"finance:{message_hash[:24]}",
        }
    if getattr(message, "attachment_refs", ()) or _DOCUMENT_RE.search(text):
        return {
            "route_ref": _stable_ref("route_document", message_hash),
            "route_type": "document_evidence",
            "target_ref": f"document:{message_hash[:24]}",
        }
    return None


def _github_cleanup_authority(message: Any) -> dict[str, bool]:
    labels = " ".join(getattr(message, "labels", ()) or ())
    text = f"{getattr(message, 'subject', '')} {getattr(message, 'body_preview', '')} {labels}"
    candidate = _GITHUB_TECH_RE.search(text) is not None
    headers = getattr(message, "headers", None) or {}
    authorized = (
        candidate
        and bool(headers.get("x-skeleton-github-authority-ref"))
        and bool(headers.get("x-skeleton-durable-handoff-ref"))
        and headers.get("x-skeleton-authority-confidence") == "1.0"
    )
    return {"candidate": candidate, "authorized": authorized}


def _account_receipt(account: MailProviderAccount, status: str, reason: str) -> dict[str, Any]:
    return {
        "account_ref": account.account_ref,
        "provider": account.provider,
        "status": status,
        "reason": reason,
        "public_safe": True,
        "private_payloads_included": False,
    }


def _message_outcome(
    *,
    new: bool = False,
    duplicate: bool = False,
    operator_packet_created: bool = False,
    scheduler_checkpoint_created: bool = False,
    private_route_created: bool = False,
    github_cleanup_candidate: bool = False,
    github_cleanup_authorized: bool = False,
) -> dict[str, bool]:
    return {
        "new": new,
        "duplicate": duplicate,
        "operator_packet_created": operator_packet_created,
        "scheduler_checkpoint_created": scheduler_checkpoint_created,
        "private_route_created": private_route_created,
        "github_cleanup_candidate": github_cleanup_candidate,
        "github_cleanup_authorized": github_cleanup_authorized,
    }


def _stable_ref(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest[:24]}"


def _timestamp(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MailRuntimeError("INVALID_TIMESTAMP", f"{field} must be a non-negative integer")
    return value
