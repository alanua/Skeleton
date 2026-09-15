from __future__ import annotations

import json

from core.mail_operations import process_important_mail
from core.mail_provider import MailProviderAccount, StaticMailProvider
from core.mail_runtime import MailRuntime, build_mail_poll_payload
from core.mail_state import MailStateStore
from integrations.mail_telegram import build_mail_telegram_handoff


PRIVATE_MARKER = "SYNTHETIC_PRIVATE_MAIL_MARKER_DO_NOT_SERIALIZE"


def _mail(**updates):
    value = {
        "provider": "synthetic",
        "provider_message_ref": "synthetic-message-1",
        "thread_ref": "synthetic-thread-1",
        "sender_ref": "synthetic-sender",
        "received_at": 1786400000,
        "subject_hint": "Routine correspondence",
        "body_preview": "Please review this synthetic message.",
        "deadline_hint": None,
        "security_metadata": {},
    }
    value.update(updates)
    return value


def _account() -> MailProviderAccount:
    return MailProviderAccount.from_mapping(
        {
            "schema": "skeleton.mail_provider_account.v1",
            "account_ref": "acct:static",
            "provider": "static",
            "poll_interval_seconds": 60,
            "max_messages_per_poll": 10,
            "query": "synthetic",
        }
    )


def test_auth_pass_with_own_domain_pattern_does_not_auto_trust() -> None:
    receipt = process_important_mail(
        _mail(
            subject_hint="Important notice from your own domain",
            body_preview="Action required from your own domain. Deadline 2026-09-01.",
            security_metadata={
                "sender_domain": "billing.example.invalid",
                "reply_to_domain": "collector.example.test",
                "authentication": {"spf": "PASS", "dkim": "PASS", "dmarc": "PASS"},
            },
        ),
        now=1786400010,
    )

    assessment = receipt["security_assessment"]
    assert assessment["category"] == "PHISHING"
    assert "AUTHENTICATION_PASS_EVIDENCE_ONLY" in assessment["reason_codes"]
    assert "OWN_DOMAIN_IMPERSONATION_PATTERN" in assessment["reason_codes"]
    assert receipt["status"] == "NEEDS_OPERATOR"


def test_sender_contact_payment_domain_divergence_raises_bounded_finding() -> None:
    receipt = process_important_mail(
        _mail(
            subject_hint="Invoice payment",
            body_preview="Please pay this synthetic invoice.",
            security_metadata={
                "sender_domain": "billing.example.invalid",
                "contact_domains": ["support.example.test"],
                "payment_domains": ["pay.example.test"],
            },
        ),
        now=1786400010,
    )

    codes = set(receipt["security_assessment"]["reason_codes"])
    assert "SENDER_CONTACT_DOMAIN_DIVERGENCE" in codes
    assert "SENDER_PAYMENT_DOMAIN_DIVERGENCE" in codes
    assert receipt["security_assessment"]["category"] == "PHISHING"


def test_fake_inkasso_payment_pressure_creates_one_case_update_and_action_packet(tmp_path) -> None:
    runtime = MailRuntime(
        state_store=MailStateStore(tmp_path / "mail.sqlite3"),
        providers={
            "static": StaticMailProvider(
                [
                    _mail(
                        subject_hint="Inkasso final notice",
                        body_preview="Private collector payment demand. Pay immediately within 24 hours or court action.",
                        security_metadata={
                            "sender_domain": "collector.example.invalid",
                            "payment_domains": ["pay.example.test"],
                        },
                    )
                ]
            )
        },
        clock=lambda: 1786400010,
    )
    payload = build_mail_poll_payload(_account())["task_packet"]

    first = runtime.process_poll_packet(payload)
    second = runtime.process_poll_packet(payload)

    assert first["processed"] == 1
    assert first["needs_operator"] == 1
    assert second["processed"] == 0
    assert second["replayed"] == 1
    receipt = first["message_receipts"][0]
    assessment = receipt["security_assessment"]
    assert assessment["category"] == "PSEUDO_INKASSO"
    assert assessment["case_update"]["state"] == "PSEUDO_INKASSO"
    assert receipt["operator_packet"]["telegram_reply_contract"]["actionable"] is True
    assert receipt["operator_packet"]["draft_ref"] is None
    handoff = build_mail_telegram_handoff(receipt["operator_packet"])
    assert handoff["allowed_actions"].count("open_private_case") == 1


def test_genuine_court_notice_routes_priority_needs_operator_never_spam() -> None:
    receipt = process_important_mail(
        _mail(
            subject_hint="Official legal notice",
            body_preview="Court notice with case number and deadline 2026-09-01.",
            security_metadata={"sender_domain": "court.example.invalid"},
        ),
        now=1786400010,
    )

    assert receipt["status"] == "NEEDS_OPERATOR"
    assert receipt["security_assessment"]["category"] == "OFFICIAL_LEGAL_NOTICE"
    assert receipt["security_assessment"]["risk_level"] == "PRIORITY"
    assert receipt["reason"] == "MAIL_SECURITY_REVIEW_REQUIRED"


def test_identity_misuse_claim_creates_evidence_case() -> None:
    receipt = process_important_mail(
        _mail(
            subject_hint="Contract in your name",
            body_preview="A synthetic contract in your name indicates identity misuse.",
        ),
        now=1786400010,
    )

    assessment = receipt["security_assessment"]
    assert assessment["category"] == "IDENTITY_MISUSE_SUSPECTED"
    assert assessment["case_update"]["state"] == "IDENTITY_MISUSE_SUSPECTED"
    assert assessment["evidence_search_request"]["intent"] == "SEARCH_PRIVATE_CORRESPONDENCE_CASE_AND_DOCUMENT_HISTORY"


def test_claimed_contract_without_history_requests_evidence_search_not_fraud_verdict() -> None:
    receipt = process_important_mail(
        _mail(
            subject_hint="Contract payment",
            body_preview="Synthetic contract payment demand for an order.",
            security_metadata={"sender_domain": "billing.example.invalid"},
        ),
        now=1786400010,
    )

    assessment = receipt["security_assessment"]
    assert assessment["evidence_search_request"]["scope"] == "PRIVATE_LOCAL_CASE_CORRESPONDENCE_DOCUMENT_HISTORY_ONLY"
    assert "CLAIMED_HISTORY_EVIDENCE_NOT_AVAILABLE_LOCALLY" in assessment["reason_codes"]
    assert "AUTOMATIC_FRAUD_VERDICT" not in assessment["reason_codes"]


def test_invoice_and_technical_routing_remain_compatible() -> None:
    invoice = process_important_mail(_mail(subject_hint="Invoice payment"), now=1786400010)
    technical = process_important_mail(_mail(subject_hint="Technical GitHub incident"), now=1786400010)

    assert invoice["operator_packet"]["policy"]["category"] == "invoice"
    assert invoice["security_assessment"]["category"] == "INVOICE_PAYMENT"
    assert technical["operator_packet"]["policy"]["category"] == "technical"
    assert technical["security_assessment"]["category"] == "TECHNICAL"


def test_routine_spam_produces_zero_telegram_notification() -> None:
    receipt = process_important_mail(
        _mail(
            subject_hint="Limited offer newsletter",
            body_preview="Routine synthetic newsletter. Unsubscribe from marketing preference.",
        ),
        now=1786400010,
    )

    assert receipt["status"] == "IGNORED"
    assert receipt["security_assessment"]["category"] == "SPAM"
    assert receipt["operator_packet"] is None


def test_duplicate_security_scan_is_deterministic() -> None:
    packet = _mail(
        subject_hint="Identity theft warning",
        body_preview="Account opened in your name. Action required.",
    )

    first = process_important_mail(packet, now=1786400010)
    second = process_important_mail(packet, now=1786400099)

    assert first["security_assessment"]["assessment_ref"] == second["security_assessment"]["assessment_ref"]
    assert first["security_assessment"]["case_update"]["update_ref"] == second["security_assessment"]["case_update"]["update_ref"]
    assert first["operator_packet"]["correspondence_ref"] == second["operator_packet"]["correspondence_ref"]


def test_private_marker_absent_from_public_receipt_serialization() -> None:
    receipt = process_important_mail(
        _mail(
            subject_hint="Important invoice",
            body_preview=f"Invoice deadline 2026-09-01 {PRIVATE_MARKER}",
            security_metadata={
                "sender_domain": "billing.example.invalid",
                "private_evidence_refs": [f"private:{PRIVATE_MARKER}"],
            },
        ),
        now=1786400010,
    )

    serialized = json.dumps(receipt, sort_keys=True)
    assert PRIVATE_MARKER not in serialized
    assert "synthetic-message-1" not in serialized
