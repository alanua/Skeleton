from __future__ import annotations

from core.family_document_report import render_package_report


def test_rich_report_contains_human_fields_not_record_hashes() -> None:
    records = [
        {
            "record_id": "doc-secret-technical-id",
            "record_hash": "deadbeef",
            "page_count": 3,
            "classification": {
                "route": "ACCEPT",
                "title": "Лист від Jobcenter",
                "issuer": "Jobcenter",
                "principal_subject_alias": "family-member-1",
                "document_type": "лист",
                "topic_alias": "04 work_tax_and_business",
                "summary": "Потрібно надати запитані документи до зазначеного строку.",
                "confidence": 0.93,
                "storage_label": "family_documents/doc-1",
            },
        }
    ]

    messages = render_package_report(records)

    assert len(messages) == 1
    message = messages[0]
    assert "Сканування завершено" in message
    assert "Лист від Jobcenter" in message
    assert "Коротко:" in message
    assert "Сторінок: 3" in message
    assert "93%" in message
    assert "doc-secret-technical-id" not in message
    assert "deadbeef" not in message


def test_low_confidence_is_visibly_review_required() -> None:
    messages = render_package_report(
        [
            {
                "page_count": 1,
                "classification": {
                    "route": "REVIEW",
                    "document_type": "лист",
                    "summary": "Недостатньо даних для надійної класифікації.",
                    "confidence": 0.45,
                    "review_reason": "невпевнена класифікація",
                },
            }
        ]
    )

    assert "Потрібна перевірка" in messages[0]
    assert "невпевнена класифікація" in messages[0]


def test_report_splits_below_telegram_limit() -> None:
    records = [
        {
            "page_count": 1,
            "classification": {
                "route": "ACCEPT",
                "title": f"Документ {index}",
                "summary": "x" * 700,
                "confidence": 0.9,
            },
        }
        for index in range(12)
    ]

    messages = render_package_report(records, max_chars=1000)

    assert len(messages) > 1
    assert all(0 < len(message) <= 1000 for message in messages)
