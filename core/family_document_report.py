from __future__ import annotations

from typing import Any, Mapping, Sequence

MAX_TELEGRAM_MESSAGE = 4096


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _confidence_text(value: object) -> str:
    if isinstance(value, Mapping):
        value = value.get("overall")
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        numeric = float(value)
        if 0 <= numeric <= 1:
            return f"{round(numeric * 100)}%"
    return ""


def _document_title(classification: Mapping[str, Any]) -> str:
    parts = [
        _text(classification.get("document_type")),
        _text(classification.get("issuer")),
    ]
    title = " — ".join(part for part in parts if part)
    return title or "Документ"


def render_document_section(record: Mapping[str, Any]) -> str:
    classification = record.get("classification")
    if not isinstance(classification, Mapping):
        classification = {}
    lines = [f"📄 {_document_title(classification)}"]
    fields = (
        ("Відправник", classification.get("issuer")),
        ("Для", classification.get("principal_subject_alias")),
        ("Тип", classification.get("document_type")),
        ("Тема", classification.get("topic_alias")),
        ("Сторінок", record.get("page_count")),
    )
    for label, value in fields:
        value_text = _text(value)
        if value_text:
            lines.append(f"{label}: {value_text}")
    confidence = _confidence_text(classification.get("confidence"))
    if confidence:
        lines.append(f"Впевненість: {confidence}")
    summary = _text(classification.get("summary"))
    if summary:
        lines.append(f"Коротко: {summary[:900]}")
    route = _text(classification.get("route")).upper()
    reasons = classification.get("reason_codes")
    review_required = route in {"REVIEW", "QUARANTINED"}
    if isinstance(reasons, Sequence) and not isinstance(reasons, (str, bytes)):
        review_required = review_required or bool(reasons)
    if review_required:
        lines.append("⚠️ Потрібна перевірка")
    storage_label = _text(record.get("storage_label"))
    if storage_label:
        lines.append(f"Збережено: {storage_label}")
    return "\n".join(lines)


def render_package_report(records: Sequence[Mapping[str, Any]]) -> list[str]:
    sections = [render_document_section(record) for record in records]
    total_pages = sum(int(record.get("page_count") or 0) for record in records)
    header = f"✅ Сканування завершено\nДокументів: {len(records)} · сторінок: {total_pages}"
    parts: list[str] = []
    current = header
    for section in sections:
        candidate = f"{current}\n\n{section}"
        if len(candidate) <= MAX_TELEGRAM_MESSAGE:
            current = candidate
            continue
        parts.append(current)
        current = section
    if current:
        parts.append(current)
    return parts
