from __future__ import annotations

from core.translation.ocr_gate import OcrGatePolicy, OcrGateState, evaluate_ocr


def test_clean_when_structure_and_confidence_are_good() -> None:
    text = "Bescheid vom 04.09.2026\nAktenzeichen 12 A 345/26\nBetrag 1405,00 EUR"
    result = evaluate_ocr(text, token_confidences=[0.98] * 9)
    assert result.state == OcrGateState.OCR_CLEAN
    assert result.shadow_only is True
    assert result.evidence.issues == ()


def test_missing_token_confidence_is_uncertain_not_trusted_clean() -> None:
    text = "Bescheid vom 04.09.2026\nAktenzeichen 12 A 345/26\nBetrag 1405,00 EUR"
    result = evaluate_ocr(text)
    assert result.state == OcrGateState.OCR_UNCERTAIN
    assert "token_confidence_unavailable" in result.evidence.issues


def test_replacement_glyph_corruption_fails_closed() -> None:
    text = "Bescheid \ufffd\ufffd\ufffd Zahlung 1405,00 EUR, Aktenzeichen 12 A 345/26."
    result = evaluate_ocr(text, token_confidences=[0.99] * 8)
    assert result.state == OcrGateState.OCR_CORRUPT
    assert "corrupt_glyph_ratio" in result.evidence.issues


def test_low_confidence_ratio_fails_closed() -> None:
    text = "Medizinischer Bericht mit Diagnosecode A00.1 und weiterer Beschreibung."
    result = evaluate_ocr(text, token_confidences=[0.30, 0.40, 0.50, 0.95, 0.98, 0.99, 0.99, 0.99])
    assert result.state == OcrGateState.OCR_CORRUPT
    assert "low_token_confidence_ratio" in result.evidence.issues


def test_partial_confidence_coverage_is_uncertain() -> None:
    text = "Dies ist ein ausreichend langer OCR Text mit mehreren stabilen Tokens und zwei Zeilen.\nZweite Zeile."
    result = evaluate_ocr(text, token_confidences=[0.99, 0.99])
    assert result.state == OcrGateState.OCR_UNCERTAIN
    assert "insufficient_confidence_coverage" in result.evidence.issues


def test_tiny_ocr_output_is_corrupt_terminal_classification() -> None:
    result = evaluate_ocr("EUR 5", token_confidences=[0.99, 0.99])
    assert result.state == OcrGateState.OCR_CORRUPT
    assert "insufficient_text" in result.evidence.issues


def test_custom_policy_is_supported_without_global_state() -> None:
    policy = OcrGatePolicy(minimum_non_whitespace_chars=5, minimum_token_confidence_coverage=0.0)
    result = evaluate_ocr("ABCDE FGH", token_confidences=[0.99, 0.99], policy=policy)
    assert result.state == OcrGateState.OCR_CLEAN
