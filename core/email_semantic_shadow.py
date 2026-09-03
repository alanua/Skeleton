from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

_QUOTED_PATTERNS = (
    re.compile(r"(?im)^On .+ wrote:\s*$"),
    re.compile(r"(?im)^Am .+ schrieb .+:\s*$"),
    re.compile(r"(?im)^В .+ писал\(а\):\s*$"),
    re.compile(r"(?im)^У .+ писав\(ла\):\s*$"),
    re.compile(r"(?im)^-----Original Message-----\s*$"),
)
_SIGNATURE = re.compile(r"(?m)^--\s*$")
_OUTLOOK_BLOCK = re.compile(r"(?im)^(?:From|Von|Від|От):\s*.+\n(?:Sent|Gesendet|Надіслано|Отправлено):\s*.+\n(?:To|An|Кому):\s*.+(?:\n(?:Subject|Betreff|Тема):\s*.+)?")
_HTML_TAG = re.compile(r"<[^>]+>")
_SUBJECT_PREFIX = re.compile(r"(?i)^\s*(?:re|fwd?|aw|wg)\s*:\s*")
_WS = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


def normalize_message_text(text: str | None) -> str:
    value = html.unescape(text or "").replace("\r\n", "\n").replace("\r", "\n")
    cut = len(value)
    for pattern in _QUOTED_PATTERNS:
        match = pattern.search(value)
        if match:
            cut = min(cut, match.start())
    outlook = _OUTLOOK_BLOCK.search(value)
    if outlook:
        cut = min(cut, outlook.start())
    sig = _SIGNATURE.search(value)
    if sig:
        cut = min(cut, sig.start())
    value = value[:cut]
    lines = []
    for line in value.splitlines():
        if line.lstrip().startswith(">"):
            continue
        lines.append(_WS.sub(" ", line).strip())
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def normalize_subject(subject: str | None) -> str:
    value = html.unescape(subject or "").strip()
    previous = None
    while value and value != previous:
        previous = value
        value = _SUBJECT_PREFIX.sub("", value).strip()
    return value


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"(?is)<(?:br|/p|/div|/li|/tr|/h[1-6])[^>]*>", "\n", value)
    text = _HTML_TAG.sub(" ", text)
    return normalize_message_text(html.unescape(text))


@dataclass(frozen=True)
class ThreadRecord:
    thread_id: str
    subject: str
    participants: tuple[str, ...]
    message_ids: tuple[str, ...]
    digest: str
    native_labels: tuple[str, ...]
    internal_ms_min: int
    internal_ms_max: int
    message_count: int
    digest_truncated: bool


def aggregate_threads(rows: Iterable[Mapping[str, object]], max_chars_per_message: int = 1200, max_chars_per_thread: int = 3000) -> list[ThreadRecord]:
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        tid = str(row.get("thread_id") or row.get("message_id") or "").strip()
        if tid:
            grouped.setdefault(tid, []).append(row)
    result: list[ThreadRecord] = []
    for tid, items in grouped.items():
        items = sorted(items, key=lambda r: int(r.get("internal_ms") or 0))
        parts: list[str] = []
        participants: set[str] = set()
        labels: set[str] = set()
        mids: list[str] = []
        subject = ""
        times: list[int] = []
        for row in items:
            mids.append(str(row.get("message_id") or ""))
            times.append(int(row.get("internal_ms") or 0))
            if not subject:
                subject = normalize_subject(str(row.get("subject") or ""))
            for key in ("from_addr", "to_addr"):
                value = str(row.get(key) or "").strip()
                if value:
                    participants.add(value)
            raw_labels = row.get("label_ids")
            if isinstance(raw_labels, str):
                labels.update(x for x in raw_labels.split(",") if x)
            raw_body = row.get("body_text") or html_to_text(str(row.get("body_html") or "")) or row.get("snippet") or ""
            text = normalize_message_text(str(raw_body))
            if text:
                parts.append(text[:max_chars_per_message])
        prefix = ("Subject: " + subject + "\n\n") if subject else ""
        # Preserve early context and the newest state for long conversations.
        if len(parts) > 3:
            selected = parts[:2] + [parts[-1]]
        else:
            selected = parts
        raw_digest = prefix + "\n\n---\n\n".join(selected)
        digest_truncated = len(raw_digest) > max_chars_per_thread or len(parts) > len(selected)
        digest = raw_digest[:max_chars_per_thread]
        result.append(ThreadRecord(
            thread_id=tid,
            subject=subject,
            participants=tuple(sorted(participants)),
            message_ids=tuple(mids),
            digest=digest.strip(),
            native_labels=tuple(sorted(labels)),
            internal_ms_min=min(times) if times else 0,
            internal_ms_max=max(times) if times else 0,
            message_count=len(items),
            digest_truncated=digest_truncated,
        ))
    return result


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("embedding_dimension_mismatch")
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    na = math.sqrt(sum(float(x) * float(x) for x in a))
    nb = math.sqrt(sum(float(y) * float(y) for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


@dataclass(frozen=True)
class PrototypePrediction:
    label: str | None
    similarity: float
    margin: float
    confidence_tier: str


def prototype_predict(vector: Sequence[float], prototypes: Mapping[str, Sequence[Sequence[float]]], *, high_similarity: float = 0.82, high_margin: float = 0.08, mid_similarity: float = 0.72) -> PrototypePrediction:
    scored: list[tuple[str, float]] = []
    for label, vectors in prototypes.items():
        if not vectors:
            continue
        score = max(cosine_similarity(vector, p) for p in vectors)
        scored.append((label, score))
    if not scored:
        return PrototypePrediction(None, 0.0, 0.0, "LOW")
    scored.sort(key=lambda x: x[1], reverse=True)
    label, best = scored[0]
    second = scored[1][1] if len(scored) > 1 else -1.0
    margin = best - second
    if best >= high_similarity and margin >= high_margin:
        tier = "HIGH"
    elif best >= mid_similarity:
        tier = "MID"
    else:
        tier = "LOW"
    return PrototypePrediction(label, best, margin, tier)
