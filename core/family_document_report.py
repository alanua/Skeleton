from __future__ import annotations

from typing import Any, Mapping, Sequence


TELEGRAM_MAX_MESSAGE_CHARS = 4096
DEFAULT_SUMMARY_LIMIT = 700


def render_package_report(
    records: Sequence[Mapping[str, Any]],
    *,
    max_chars: int = TELEGRAM_MAX_MESSAGE_CHARS,
) -> list[str]:
    if not 256 <= max_chars <= TELEGRAM_MAX_MESSAGE_CHARS:
        raise ValueError("report_message_limit_invalid")
    if not records:
        return []

    total_pages = sum(_positive_int(record.get("page_count"), default=1) for record in records)
    review_count = sum(1 for record in records if _needs_review(record))
    header = [
        "Сканування завершено",
        f"Документів: {len(records)}",
        f"Сторінок: {total_pages}",
    ]
    if review_count:
        header.append(f"Потрібна перевірка: {review_count}")
    header_text = "\n".join(header)

    blocks = [_document_block(index, record) for index, record in enumerate(records, start=1)]
    return _pack_blocks(header_text, blocks, max_chars=max_chars)


def _document_block(index: int, record: Mapping[str, Any]) -> str:
    classification = record.get("classification")
    if not isinstance(classification, Mapping):
        classification = {}

    title = _clean_text(classification.get("title"), limit=180)
    document_type = _clean_text(classification.get("document_type"), limit=120)
    issuer = _clean_text(classification.get("issuer"), limit=160)
    owner = _clean_text(classification.get("principal_subject_alias"), limit=120)
    topic = _clean_text(classification.get("topic_alias"), limit=120)
    summary = _clean_text(classification.get("summary"), limit=DEFAULT_SUMMARY_LIMIT)
    storage_label = _clean_text(classification.get("storage_label"), limit=180)
    review_reason = _clean_text(classification.get("review_reason"), limit=220)
    page_count = _positive_int(record.get("page_count"), default=1)
    confidence = _confidence_text(classification.get("confidence"))
    needs_review = _needs_review(record)

    display_title = title or document_type or (f"Документ від {issuer}" if issuer else f"Документ {index}")
    lines = [f"{index}. {display_title}"]
    if issuer:
        lines.append(f"Відправник: {issuer}")
    if owner:
        lines.append(f"Кому: {owner}")
    if document_type and document_type != display_title:
        lines.append(f"Тип: {document_type}")
    if topic:
        lines.append(f"Тема: {topic}")
    lines.append(f"Сторінок: {page_count}")
    if summary:
        lines.append(f"Коротко: {summary}")
    if confidence:
        lines.append(f"Впевненість: {confidence}")
    if storage_label:
        lines.append(f"Збережено: {storage_label}")
    if needs_review:
        suffix = f" — {review_reason}" if review_reason else ""
        lines.append(f"Потрібна перевірка{suffix}")
    return "\n".join(lines)


def _needs_review(record: Mapping[str, Any]) -> bool:
    classification = record.get("classification")
    if not isinstance(classification, Mapping):
        return True
    explicit = classification.get("review_required")
    if explicit is True:
        return True
    route = str(classification.get("route", "")).strip().upper()
    if route and route not in {"ACCEPT", "DONE", "AUTO_ACCEPT"}:
        return True
    confidence = classification.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        return float(confidence) < 0.70
    return False


def _pack_blocks(header: str, blocks: Sequence[str], *, max_chars: int) -> list[str]:
    messages: list[str] = []
    current = header
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            messages.append(current)
        if len(block) <= max_chars:
            current = block
            continue
        pieces = _split_long_block(block, max_chars=max_chars)
        messages.extend(pieces[:-1])
        current = pieces[-1]
    if current:
        messages.append(current)
    return messages


def _split_long_block(block: str, *, max_chars: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for line in block.splitlines():
        safe_line = line
        while len(safe_line) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(safe_line[:max_chars])
            safe_line = safe_line[max_chars:]
        candidate = f"{current}\n{safe_line}" if current else safe_line
        if len(candidate) > max_chars:
            pieces.append(current)
            current = safe_line
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces or [block[:max_chars]]


def _clean_text(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.replace("\x00", " ").split()).strip()
    return text[:limit]


def _positive_int(value: object, *, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def _confidence_text(value: object) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    confidence = min(max(float(value), 0.0), 1.0)
    return f"{round(confidence * 100):d}%"
