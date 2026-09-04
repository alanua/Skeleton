from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Sequence


class OcrGateState(StrEnum):
    OCR_CLEAN = "OCR_CLEAN"
    OCR_UNCERTAIN = "OCR_UNCERTAIN"
    OCR_CORRUPT = "OCR_CORRUPT"


@dataclass(frozen=True)
class OcrGatePolicy:
    """Deterministic shadow policy for OCR quality triage.

    These thresholds are engineering defaults, not calibrated production
    acceptance values. Production promotion requires evaluation evidence.
    """

    corrupt_glyph_ratio: float = 0.025
    uncertain_glyph_ratio: float = 0.010
    low_confidence_threshold: float = 0.70
    corrupt_low_confidence_ratio: float = 0.35
    uncertain_low_confidence_ratio: float = 0.15
    minimum_token_confidence_coverage: float = 0.60
    minimum_non_whitespace_chars: int = 24
    maximum_single_line_fraction: float = 0.92


@dataclass(frozen=True)
class OcrGateEvidence:
    non_whitespace_chars: int
    token_count: int
    suspicious_glyph_count: int
    suspicious_glyph_ratio: float
    confidence_sample_count: int
    confidence_coverage: float | None
    low_confidence_count: int
    low_confidence_ratio: float | None
    mean_token_confidence: float | None
    line_count: int
    longest_line_fraction: float
    issues: tuple[str, ...]


@dataclass(frozen=True)
class OcrGateResult:
    state: OcrGateState
    evidence: OcrGateEvidence
    shadow_only: bool = True


_REPLACEMENT_CHARS = {"\ufffd", "\u25a1", "\u25a0"}
_TOKEN_RE = re.compile(r"\S+")


def _normalise_confidences(values: Iterable[float] | None) -> tuple[float, ...]:
    if values is None:
        return ()
    out: list[float] = []
    for value in values:
        number = float(value)
        if not math.isfinite(number):
            continue
        if number > 1.0 and number <= 100.0:
            number /= 100.0
        if 0.0 <= number <= 1.0:
            out.append(number)
    return tuple(out)


def _is_suspicious_glyph(ch: str) -> bool:
    if ch in _REPLACEMENT_CHARS:
        return True
    category = unicodedata.category(ch)
    if category == "Cc" and ch not in "\n\r\t":
        return True
    return False


def evaluate_ocr(
    text: str,
    *,
    token_confidences: Sequence[float] | None = None,
    policy: OcrGatePolicy | None = None,
) -> OcrGateResult:
    """Classify OCR evidence without changing translation/runtime state.

    The gate is intentionally conservative when confidence metadata is missing:
    structurally sane text may be CLEAN only if no hard/soft issue exists, while
    suspicious text or insufficient confidence coverage becomes UNCERTAIN or
    CORRUPT. The function has no side effects and is safe for shadow evaluation.
    """

    policy = policy or OcrGatePolicy()
    text = text or ""
    non_ws = [ch for ch in text if not ch.isspace()]
    non_ws_count = len(non_ws)
    tokens = _TOKEN_RE.findall(text)
    token_count = len(tokens)
    suspicious = sum(1 for ch in non_ws if _is_suspicious_glyph(ch))
    suspicious_ratio = suspicious / non_ws_count if non_ws_count else 1.0

    conf = _normalise_confidences(token_confidences)
    confidence_count = len(conf)
    confidence_coverage = (
        confidence_count / token_count if token_count else (1.0 if confidence_count else None)
    )
    low_count = sum(1 for value in conf if value < policy.low_confidence_threshold)
    low_ratio = low_count / confidence_count if confidence_count else None
    mean_conf = sum(conf) / confidence_count if confidence_count else None

    lines = [line for line in text.splitlines() if line.strip()]
    line_count = len(lines)
    longest_line_chars = max((len(re.sub(r"\s+", "", line)) for line in lines), default=0)
    longest_line_fraction = longest_line_chars / non_ws_count if non_ws_count else 1.0

    hard: list[str] = []
    soft: list[str] = []

    if non_ws_count < policy.minimum_non_whitespace_chars:
        hard.append("insufficient_text")
    if suspicious_ratio >= policy.corrupt_glyph_ratio:
        hard.append("corrupt_glyph_ratio")
    elif suspicious_ratio >= policy.uncertain_glyph_ratio:
        soft.append("suspicious_glyph_ratio")

    if low_ratio is not None:
        if low_ratio >= policy.corrupt_low_confidence_ratio:
            hard.append("low_token_confidence_ratio")
        elif low_ratio >= policy.uncertain_low_confidence_ratio:
            soft.append("elevated_low_token_confidence_ratio")
        if confidence_coverage is not None and confidence_coverage < policy.minimum_token_confidence_coverage:
            soft.append("insufficient_confidence_coverage")
    else:
        soft.append("token_confidence_unavailable")

    # A very long one-line OCR stream is a layout anomaly, but never a hard fail
    # by itself because legitimate forms/receipts can be sparse.
    if non_ws_count >= 80 and longest_line_fraction >= policy.maximum_single_line_fraction:
        soft.append("layout_single_line_anomaly")

    if hard:
        state = OcrGateState.OCR_CORRUPT
        issues = tuple(hard + soft)
    elif soft:
        state = OcrGateState.OCR_UNCERTAIN
        issues = tuple(soft)
    else:
        state = OcrGateState.OCR_CLEAN
        issues = ()

    return OcrGateResult(
        state=state,
        evidence=OcrGateEvidence(
            non_whitespace_chars=non_ws_count,
            token_count=token_count,
            suspicious_glyph_count=suspicious,
            suspicious_glyph_ratio=suspicious_ratio,
            confidence_sample_count=confidence_count,
            confidence_coverage=confidence_coverage,
            low_confidence_count=low_count,
            low_confidence_ratio=low_ratio,
            mean_token_confidence=mean_conf,
            line_count=line_count,
            longest_line_fraction=longest_line_fraction,
            issues=issues,
        ),
    )
