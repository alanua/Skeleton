from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import re
import time
from typing import Any

from core.domain_event_graph import DOMAIN_EVENT_ENVELOPE_SCHEMA, DomainEventGraph
from core.mail_operations import process_important_mail, public_mail_operation_receipt
from core.mail_provider import (
    AUTH_REQUIRED,
    MailCleanupAction,
    MailProvider,
    MailProviderAccount,
    MailProviderError,
    ProviderMailMessage,
    provider_alias,
    stable_hash,
)
from core.mail_state import MailRuntimeState
from integrations.mail_scheduler import MailScheduler
from integrations.mail_telegram import MailTelegramEmitter


MAIL_RUNTIME_RECEIPT_SCHEMA = "skeleton.mail_runtime.receipt.v1"
MAIL_HANDOFF_SCHEMA = "skeleton.mail_runtime.private_handoff_receipt.v1"
TECHNICAL_GITHUB_CLEANUP_CLASSES = frozenset({"github_ci_success", "github_ci_routine"})


@dataclass(frozen=True)
class MailClassification:
    classification: str
    important: bool
    needs_operator: bool
    routine: bool
    cleanup_candidate: bool
    github_authority_ref: str | None = None
    invoice: bool = False
    document: bool = False
    reason: str = "CLASSIFIED"


class MailRuntime:
    def __init__(
        self,
        *,
        state: MailRuntimeState,
        provider: MailProvider,
        account: MailProviderAccount,
        scheduler: MailScheduler,
        telegram: MailTelegramEmitter,
        domain_event_graph: DomainEventGraph | None = None,
        clock: Any | None = None,
        scan_limit: int = 50,
    ) -> None:
        self.state = state
        self.provider = provider
        self.account = account
        self.scheduler = scheduler
        self.telegram = telegram
        self.domain_event_graph = domain_event_graph or DomainEventGraph()
        self.clock = clock or time.time
        self.scan_limit = scan_limit

    def health(self) -> dict[str, Any]:
        self.state.initialize()
        return {
            "schema": "skeleton.mail_runtime.health.v1",
            "status": "READY",
            "provider_alias": provider_alias(self.account),
            "public_safe": True,
            "private_payloads_included": False,
        }

    def scan_once(self, *, now: int | None = None) -> dict[str, Any]:
        current = int(self.clock()) if now is None else now
        alias = provider_alias(self.account)
        self.state.initialize()
        counters: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        try:
            scan = self.provider.scan(self.account, cursor=self.state.get_cursor(alias), limit=self.scan_limit)
        except MailProviderError as exc:
            if exc.reason_code == AUTH_REQUIRED:
                return self._receipt("AUTH_REQUIRED", "AUTH_REQUIRED", counters, reasons, alias)
            raise

        for change in scan.changes:
            try:
                message = self.provider.fetch_message(
                    self.account, provider_message_ref=change.provider_message_ref
                )
            except MailProviderError as exc:
                reasons[exc.reason_code] += 1
                counters["failed"] += 1
                continue
            outcome = self._process_message(message, now=current)
            counters[outcome["counter"]] += 1
            reasons[outcome["reason"]] += 1

        self.state.set_cursor(alias, scan.cursor, now=current)
        status = "DONE" if not counters.get("failed") else "PARTIAL"
        return self._receipt(status, "SCAN_COMPLETED", counters, reasons, alias)

    def send_message(self, *_args: Any, **_kwargs: Any) -> Mapping[str, Any]:
        raise MailProviderError("SEND_DISABLED", "external mail send is disabled")

    def _process_message(self, message: ProviderMailMessage, *, now: int) -> dict[str, str]:
        alias = provider_alias(self.account)
        message_hash = message.content_fingerprint()
        existing = self.state.get_handoff(message_hash)
        if existing is not None:
            return {"counter": "duplicate", "reason": "DUPLICATE_EXISTING"}

        classification = classify_message(message)
        operations_receipt = process_important_mail(message.envelope_for_operations(), now=now)
        if classification.needs_operator and operations_receipt["status"] == "IGNORED":
            operations_receipt = _promote_needs_operator(operations_receipt)
        handoff_ref = f"mail_handoff:{stable_hash({'message': message_hash, 'alias': alias})[:24]}"
        handoff_receipt = {
            "schema": MAIL_HANDOFF_SCHEMA,
            "provider_alias": alias,
            "message_hash": message_hash,
            "classification": classification.classification,
            "case_ref": operations_receipt.get("case_ref"),
            "correspondence_ref": operations_receipt.get("correspondence_ref"),
            "durable": True,
            "public_safe": True,
            "private_payloads_included": False,
        }
        handoff, created = self.state.record_handoff(
            message_hash=message_hash,
            provider_alias=alias,
            classification=classification.classification,
            case_ref=_optional_str(operations_receipt.get("case_ref")),
            correspondence_ref=_optional_str(operations_receipt.get("correspondence_ref")),
            handoff_ref=handoff_ref,
            receipt=handoff_receipt,
            now=now,
        )
        if not created:
            return {"counter": "duplicate", "reason": "DUPLICATE_EXISTING"}

        self._ingest_domain_events(message, classification, operations_receipt, now=now)
        checkpoint = operations_receipt.get("scheduler_checkpoint")
        if isinstance(checkpoint, Mapping):
            schedule_id = str(checkpoint["schedule_id"])
            if self.state.record_scheduler_checkpoint(
                schedule_id=schedule_id, message_hash=message_hash, checkpoint=checkpoint, now=now
            ):
                self.scheduler.register_deadline_checkpoint(checkpoint, now=now)

        packet = operations_receipt.get("operator_packet")
        if classification.needs_operator and isinstance(packet, Mapping):
            packet_ref = f"mail_packet:{stable_hash({'message': message_hash, 'kind': 'operator'})[:24]}"
            if self.state.record_operator_packet(packet_ref=packet_ref, message_hash=message_hash, packet=packet, now=now):
                self.telegram.emit_operator_packet(packet, packet_ref=packet_ref)

        if classification.cleanup_candidate:
            self._cleanup_after_handoff(message, handoff.durable, classification, now=now)

        return {"counter": "processed", "reason": classification.reason}

    def _ingest_domain_events(
        self,
        message: ProviderMailMessage,
        classification: MailClassification,
        operations_receipt: Mapping[str, Any],
        *,
        now: int,
    ) -> None:
        refs: list[dict[str, str]] = [{"ref_type": "mail", "ref_id": str(operations_receipt["correspondence_ref"])}]
        case_ref = operations_receipt.get("case_ref")
        if isinstance(case_ref, str):
            refs.append({"ref_type": "case", "ref_id": case_ref})
        domain = "mail"
        event_type = "mail_correspondence_handoff"
        if classification.github_authority_ref is not None:
            domain = "github"
            event_type = "github_ci_mail_correlated"
            refs.append({"ref_type": "github_ci_mail", "ref_id": classification.github_authority_ref})
        if classification.invoice:
            refs.extend(
                [
                    {"ref_type": "mail_invoice", "ref_id": f"mail_invoice:{message.content_fingerprint()[:24]}"},
                    {"ref_type": "finance", "ref_id": f"finance:{message.content_fingerprint()[:24]}"},
                    {"ref_type": "gewerbe", "ref_id": f"gewerbe:{message.content_fingerprint()[:24]}"},
                ]
            )
        if classification.document:
            refs.append({"ref_type": "document", "ref_id": f"document:{message.content_fingerprint()[:24]}"})
        self.domain_event_graph.ingest(
            {
                "schema": DOMAIN_EVENT_ENVELOPE_SCHEMA,
                "domain": domain,
                "event_type": event_type,
                "source_ref": f"mail:{message.content_fingerprint()[:24]}",
                "observed_at": now,
                "idempotency_key": f"mail:{message.content_fingerprint()}",
                "refs": refs,
                "provenance_refs": [{"ref": f"mail:{message.content_fingerprint()[:24]}", "kind": "private_mail"}],
                "confidence": 1.0 if classification.github_authority_ref else 0.9,
                "inferred": classification.github_authority_ref is None,
            }
        )

    def _cleanup_after_handoff(
        self,
        message: ProviderMailMessage,
        durable: bool,
        classification: MailClassification,
        *,
        now: int,
    ) -> None:
        if not durable or not self.account.cleanup_enabled:
            return
        if classification.classification not in TECHNICAL_GITHUB_CLEANUP_CLASSES:
            return
        if classification.github_authority_ref is None:
            return
        action = MailCleanupAction(
            provider_message_ref=message.provider_message_ref,
            action="label",
            label=self.account.label_after_handoff or "skeleton-handoff",
        )
        self.provider.apply_cleanup(self.account, actions=(action,))
        cleanup_ref = f"mail_cleanup:{stable_hash({'message': message.content_fingerprint(), 'action': action.action})[:24]}"
        self.state.record_cleanup(
            cleanup_ref=cleanup_ref,
            message_hash=message.content_fingerprint(),
            action=action.action,
            now=now,
        )

    def _receipt(
        self,
        status: str,
        reason: str,
        counters: Counter[str],
        reasons: Counter[str],
        alias: str,
    ) -> dict[str, Any]:
        counts = self.state.counts() if status != "AUTH_REQUIRED" else {}
        return public_mail_operation_receipt(
            {
                "schema": MAIL_RUNTIME_RECEIPT_SCHEMA,
                "status": status,
                "reason": reason,
                "provider_alias": alias,
                "scan_counts": dict(counters),
                "reason_counts": dict(reasons),
                "state_counts": counts,
                "public_safe": True,
                "private_payloads_included": False,
                "external_mail_send_enabled": False,
            }
        )


def classify_message(message: ProviderMailMessage) -> MailClassification:
    text = f"{message.subject} {message.body_preview}".lower()
    github_ref = correlate_github_authority(message)
    if github_ref is not None:
        success = any(token in text for token in ("fixed", "passed", "succeeded", "successful", "completed"))
        failure = any(token in text for token in ("failed", "failure", "cancelled", "timed out", "error"))
        if failure:
            return MailClassification("github_ci_failure", True, True, False, False, github_ref, reason="GITHUB_CI_ACTIONABLE")
        return MailClassification(
            "github_ci_success" if success else "github_ci_routine",
            False,
            False,
            True,
            True,
            github_ref,
            reason="GITHUB_CI_ROUTINE_CORRELATED",
        )
    invoice = any(token in text for token in ("invoice", "rechnung", "payment", "zahlung"))
    document = invoice or any(token in text for token in ("document", "attachment", "vertrag", "contract"))
    important = message.is_flagged_important() or any(
        token in text for token in ("important", "urgent", "deadline", "action required", "frist", "due")
    )
    if important:
        return MailClassification("important", True, True, False, False, invoice=invoice, document=document, reason="IMPORTANT_MAIL")
    if invoice:
        return MailClassification("invoice", False, False, False, False, invoice=True, document=True, reason="INVOICE_ROUTED")
    if document:
        return MailClassification("document", False, False, False, False, document=True, reason="DOCUMENT_ROUTED")
    return MailClassification("routine", False, False, True, False, reason="ROUTINE_MAIL")


def correlate_github_authority(message: ProviderMailMessage) -> str | None:
    headers = {key.lower(): value for key, value in (message.headers or {}).items()}
    sender = (message.sender or "").lower()
    text = f"{message.subject}\n{message.body_preview}\n{json.dumps(headers, sort_keys=True)}".lower()
    if "github.com" not in sender and "github" not in text:
        return None
    repo_match = re.search(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b", text)
    sha_match = re.search(r"\b[0-9a-f]{40}\b", text)
    run_match = re.search(r"(?:run|workflow|check)[ #:_-]*(\d{3,})", text)
    if repo_match is None or (sha_match is None and run_match is None):
        return None
    digest_source = {
        "repo": repo_match.group(1).lower(),
        "sha": sha_match.group(0) if sha_match else None,
        "run": run_match.group(1) if run_match else None,
    }
    return f"github_ci:{stable_hash(digest_source)[:24]}"


def _promote_needs_operator(receipt: Mapping[str, Any]) -> dict[str, Any]:
    promoted = dict(receipt)
    promoted["status"] = "NEEDS_OPERATOR"
    promoted["reason"] = "CLASSIFICATION_NEEDS_OPERATOR"
    return promoted


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None
