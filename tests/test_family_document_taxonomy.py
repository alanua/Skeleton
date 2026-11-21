from __future__ import annotations

from core.family_document_taxonomy import classify_family_document_text


def test_exact_appointment_date_and_time_produces_typed_event() -> None:
    result = classify_family_document_text(
        "Jobcenter Termin Einladung für Alex am 31.08.2026 um 10:30. Bitte erscheinen.",
        ("Alex", "Maria", "Ivan"),
    )

    events = result["event_candidates"]
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "appointment"
    assert event["timezone"] == "Europe/Berlin"
    assert isinstance(event["start_at"], int)
    assert event["title"] == "Termin — Jobcenter"
    assert "CALENDAR_EVENT_AMBIGUOUS" not in result["reason_codes"]


def test_appointment_without_exact_time_does_not_fabricate_event_and_routes_review() -> None:
    result = classify_family_document_text(
        "Jobcenter Termin Einladung für Alex am 31.08.2026. Bitte erscheinen.",
        ("Alex", "Maria", "Ivan"),
    )

    assert result["event_candidates"] == []
    assert result["route"] == "REVIEW"
    assert "CALENDAR_EVENT_AMBIGUOUS" in result["reason_codes"]


def test_non_appointment_document_date_does_not_become_calendar_event() -> None:
    result = classify_family_document_text(
        "Finanzamt Bescheid für Alex vom 31.08.2026. Steuer Entscheidung.",
        ("Alex", "Maria", "Ivan"),
    )

    assert result["event_candidates"] == []
    assert "CALENDAR_EVENT_AMBIGUOUS" not in result["reason_codes"]


BANK_ALIASES = ("Subject-A", "Subject-B", "Subject-C")
BANK_TEXT = (
    "Mittelbrandenburgische Sparkasse / MBS Girovertrag\n"
    "Kontoinhaber: Subject-A\n"
    "Kontoführungsgebühr: 4,50 EUR monatlich\n"
    "Bedingungen: Kündigung schriftlich möglich.\n"
    "Hinweis zum Aufenthalt."
)


def test_mbs_contract_with_incidental_residence_token() -> None:
    result = classify_family_document_text(BANK_TEXT, BANK_ALIASES)
    assert result["route"] == "ACCEPT"
    assert result["topic_alias"] == "06 finance_banking_and_contracts"
    assert result["document_type"] == "Vertrag"
    assert result["issuer"] == "Mittelbrandenburgische Sparkasse"
    assert result["principal_subject_alias"] == "Subject-A"
    assert result["bank_contract"] == {
        "bank": "Mittelbrandenburgische Sparkasse",
        "form_type": "girovertrag",
        "product_type": "Girokonto",
        "account_owner_alias": "Subject-A",
        "fees": [{"amount": "4.50", "currency": "EUR", "period": "month"}],
        "conditions": ["Kündigung schriftlich möglich."],
    }


def test_bank_owner_requires_exact_labelled_configured_alias() -> None:
    for owner in ("", "Subject-A and Subject-B", "Subject-A-extra", "Subject-A.extra", "Subject-A; Subject-B", "Unknown", "Subject-A\nKontoinhaber: Subject-B"):
        text = BANK_TEXT.replace("Kontoinhaber: Subject-A", f"Contact: Subject-A\nKontoinhaber: {owner}")
        result = classify_family_document_text(text, BANK_ALIASES)
        assert result["route"] == "REVIEW"
        assert result["principal_subject_alias"] is None
        assert result["bank_contract"]["account_owner_alias"] is None
        assert "BANK_OWNER_AMBIGUOUS" in result["reason_codes"]


def test_bank_fee_evidence_is_never_inferred() -> None:
    for fee in ("", "see price list", "free", "4,50 EUR", "4,50 EUR monthly maybe", "4,50 EUR monatlich\nKontoführungsgebühr: 9,00 EUR monatlich"):
        result = classify_family_document_text(BANK_TEXT.replace("4,50 EUR monatlich", fee), BANK_ALIASES)
        assert result["route"] == "REVIEW"
        assert result["bank_contract"]["fees"] == []
        assert "BANK_FEES_AMBIGUOUS" in result["reason_codes"]


def test_explicit_zero_fee_and_absent_conditions() -> None:
    result = classify_family_document_text(
        BANK_TEXT.replace("4,50", "0,00").replace("Bedingungen: Kündigung schriftlich möglich.\n", ""), BANK_ALIASES
    )
    assert result["bank_contract"]["fees"][0]["amount"] == "0.00"
    assert result["bank_contract"]["conditions"] == []


def test_bank_conflicting_context_and_conditions_route_review() -> None:
    for extra, reason in (
        ("\nAufenthaltserlaubnis", "BANK_MIGRATION_CONTEXT_CONFLICT"),
        ("\nFinanzamt", "BANK_ISSUER_CONFLICT"),
        ("\nKonditionen: Andere Bedingungen.", "BANK_CONDITIONS_AMBIGUOUS"),
        ("\nKontovertrag", "BANK_FORM_AMBIGUOUS"),
    ):
        result = classify_family_document_text(BANK_TEXT + extra, BANK_ALIASES)
        assert result["route"] == "REVIEW"
        assert reason in result["reason_codes"]
        assert result["topic_alias"] == "06 finance_banking_and_contracts"
    result = classify_family_document_text(BANK_TEXT + "\nKonditionen: Andere Bedingungen.", BANK_ALIASES)
    assert result["bank_contract"]["conditions"] == []


def test_migration_requires_context_and_bank_rules_require_bank_and_form() -> None:
    for text in ("Subject-A Aufenthalt", "Subject-A MBS", "Subject-A Girovertrag"):
        result = classify_family_document_text(text, BANK_ALIASES)
        assert result["topic_alias"] != "02 migration_and_residence"
        assert "bank_contract" not in result
    result = classify_family_document_text("Ausländerbehörde Aufenthaltserlaubnis residence permit Bescheid Subject-A", BANK_ALIASES)
    assert result["topic_alias"] == "02 migration_and_residence"