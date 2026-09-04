from __future__ import annotations

from core.translation.entity_ledger import EntityClass, evaluate_entity_recall, extract_entities


def test_extracts_amount_date_case_iban_and_address_exactly() -> None:
    text = (
        "Bescheid vom 04.09.2026\n"
        "Aktenzeichen: 12 A 345/26\n"
        "Betrag 1.405,00 EUR\n"
        "IBAN DE89 3704 0044 0532 0130 00\n"
        "Anschrift: Steinstraße 22\n"
    )
    ledger = extract_entities(text)
    assert [x.text for x in ledger.by_class(EntityClass.DATE)] == ["04.09.2026"]
    assert [x.text for x in ledger.by_class(EntityClass.AMOUNT)] == ["1.405,00 EUR"]
    assert [x.text for x in ledger.by_class(EntityClass.CASE_REFERENCE)] == ["12 A 345/26"]
    assert [x.text for x in ledger.by_class(EntityClass.IBAN_ACCOUNT)] == ["DE89 3704 0044 0532 0130 00"]
    assert "Steinstraße 22" in [x.text for x in ledger.by_class(EntityClass.ADDRESS)]
    assert ledger.shadow_only is True


def test_extracts_medical_code_without_claiming_semantic_correctness() -> None:
    ledger = extract_entities("Diagnosecode M54.5 bei Vorstellung am 03.09.2026")
    assert "M54.5" in [x.text for x in ledger.by_class(EntityClass.MEDICAL_CODE)]


def test_names_are_label_anchored_in_shadow_extractor() -> None:
    ledger = extract_entities("Patient: Max Mustermann\nBehandelnder Arzt Dr. Erika Beispiel")
    assert [x.text for x in ledger.by_class(EntityClass.NAME)] == ["Max Mustermann"]


def test_recall_gate_reports_missed_entity_by_class() -> None:
    ledger = extract_entities("Aktenzeichen: 12 A 345/26\nBetrag 25,00 EUR")
    result = evaluate_entity_recall(
        ledger,
        {
            EntityClass.CASE_REFERENCE: ["12 A 345/26", "9 K 77/26"],
            EntityClass.AMOUNT: ["25,00 EUR"],
        },
    )
    case_row = next(x for x in result.classes if x.entity_class is EntityClass.CASE_REFERENCE)
    amount_row = next(x for x in result.classes if x.entity_class is EntityClass.AMOUNT)
    assert case_row.recall == 0.5
    assert case_row.missed_texts == ("9 K 77/26",)
    assert amount_row.recall == 1.0
    assert result.all_expected_found is False


def test_recall_gate_does_not_infer_gold_from_detected_entities() -> None:
    ledger = extract_entities("Betrag 25,00 EUR")
    result = evaluate_entity_recall(ledger, {EntityClass.AMOUNT: []})
    row = result.classes[0]
    assert row.recall is None
    assert row.expected == 0
    assert result.all_expected_found is True


def test_nonstandard_case_reference_is_exposed_as_recall_failure() -> None:
    # This deliberately odd unlabeled format is a regression fixture proving that
    # missed extraction is measurable rather than silently treated as safe.
    text = "Verfahren 3 O 17-24-X\nBitte beachten Sie die Frist."
    ledger = extract_entities(text)
    result = evaluate_entity_recall(ledger, {EntityClass.CASE_REFERENCE: ["3 O 17-24-X"]})
    row = result.classes[0]
    assert row.recall == 0.0
    assert row.missed_texts == ("3 O 17-24-X",)


def test_ocr_damaged_medical_code_is_exposed_as_recall_failure() -> None:
    text = "Diagnosecode M5?.5 nach OCR-Beschädigung"
    ledger = extract_entities(text)
    result = evaluate_entity_recall(ledger, {EntityClass.MEDICAL_CODE: ["M5?.5"]})
    row = result.classes[0]
    assert row.recall == 0.0
    assert row.missed_texts == ("M5?.5",)
