from __future__ import annotations

import re
import unicodedata

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


class PrivateMemoryTermsError(ValueError):
    pass


def private_terms(value: str, *, max_chars: int | None = None) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise PrivateMemoryTermsError("value must be a string")
    if max_chars is not None and len(value) > max_chars:
        raise PrivateMemoryTermsError("value exceeds the allowed length")
    if not value.strip():
        raise PrivateMemoryTermsError("value must not be empty")

    normalized = unicodedata.normalize("NFKC", value).casefold()
    terms = tuple(sorted(set(_TOKEN_RE.findall(normalized))))
    if not terms:
        raise PrivateMemoryTermsError("value has no searchable terms")
    return terms
