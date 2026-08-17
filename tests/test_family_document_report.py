from __future__ import annotations

from core.family_document_report import MAX_TELEGRAM_MESSAGE, render_package_report


def test_rich_report_is_human_readable_and_omits_technical_ids() -> None:
    records = [
        {
            "record_id": "doc-secret-technical-id",
            "record_hash": "deadbeef",
            "page_count": 3,
            "storage_label": "Сімейний архів / Податки / 2026",
            "classification": {
                "issuer": "Finanzamt",
                "principal_subject_alias": "Олексій",
                "document_type": "Bescheid",
                "topic_alias": "Податки",
                "summary": "Податкове рішення за поточний період.",
                "confidence": 0.94,
                "route": "DONE",
            },
        }
    ]

    messages = render_package_report(records)

    assert len(messages) == 1
    message = messages[0]
    assert "Сканування завершено" in message
    assert "Finanzamt" in message
    assert "Bescheid" in message
    assert "Податки" in message
    assert "Сторінок: 3" in message
    assert "doc-secret-technical-id" not in message
    assert "deadbeef" not in message


def test_low_confidence_review_is_visible() -> None:
    messages = render_package_report(
        [
            {
                "page_count": 1,
                "classification": {
                    "document_type": "Лист",
                    "route": "REVIEW",
                    "confidence": 0.41,
                    "reason_codes": ["ambiguous_subject"],
                },
            }
        ]
    )
    assert "Потрібна перевірка" in messages[0]


def test_report_parts_respect_telegram_limit() -> None:
    records = [
        {
            "page_count": 1,
            "classification": {
                "document_type": f"Документ {index}",
                "summary": "x" * 800,
            },
        }
        for index in range(12)
    ]
    messages = render_package_report(records)
    assert len(messages) > 1
    assert all(len(message) <= MAX_TELEGRAM_MESSAGE for message in messages)
