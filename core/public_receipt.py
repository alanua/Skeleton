from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping


PublicFieldKind = Literal[
    "aggregate",
    "boolean",
    "hash",
    "integer",
    "opaque_id",
    "status",
    "string",
]

MAX_PUBLIC_STRING_LENGTH = 512
_WILDCARD = "*"
_HASH_RE = re.compile(r"\b[a-f0-9]{32,128}\b")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_STATUS_RE = re.compile(r"^[A-Z][A-Z0-9_:-]{0,63}$")
_PRIVATE_FIELD_PARTS = (
    "access_token",
    "account_id",
    "api_key",
    "apikey",
    "auth_token",
    "client_secret",
    "credential",
    "password",
    "private_key",
    "provider_id",
    "provider_identifier",
    "raw_provider",
    "secret",
    "token",
)
_PRIVATE_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_=-]{16,}\b"),
    re.compile(r"\b(?:acct|provider|user|tenant)[_-]?(?:live|prod|private)[_-][A-Za-z0-9._:-]{6,}\b", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_]*token[A-Za-z0-9_]*\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_]*password[A-Za-z0-9_]*\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_]*secret[A-Za-z0-9_]*\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_]*api[_-]?key[A-Za-z0-9_]*\s*[:=]\s*\S+", re.IGNORECASE),
)
_PRIVATE_PATH_PATTERN = re.compile(
    r"(?:(?:^|\s)/(?:home|root|mnt|media|var/lib|etc|run/secrets)/|[A-Za-z]:\\Users\\)"
)
_URL_OR_EMAIL_PATTERN = re.compile(r"(?:https?://|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")


class PublicReceiptError(ValueError):
    """Raised when a public receipt cannot be rendered safely."""


@dataclass(frozen=True)
class PublicField:
    """One explicit allowlisted public receipt field path."""

    path: tuple[str, ...]
    kind: PublicFieldKind = "string"
    max_length: int = MAX_PUBLIC_STRING_LENGTH

    @classmethod
    def at(
        cls,
        path: str | Iterable[str],
        *,
        kind: PublicFieldKind = "string",
        max_length: int = MAX_PUBLIC_STRING_LENGTH,
    ) -> "PublicField":
        parts = tuple(path.split(".")) if isinstance(path, str) else tuple(path)
        if not parts or any(not part for part in parts):
            raise PublicReceiptError("public receipt allowlist path is empty")
        if max_length < 1 or max_length > MAX_PUBLIC_STRING_LENGTH:
            raise PublicReceiptError("public receipt field max_length is out of bounds")
        return cls(path=parts, kind=kind, max_length=max_length)


@dataclass(frozen=True)
class PublicReceiptBoundary:
    """Fail-closed renderer for public receipt metadata."""

    fields: tuple[PublicField, ...]

    def sanitize(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        return sanitize_public_receipt(receipt, self.fields)


def sanitize_public_receipt(
    receipt: Mapping[str, Any],
    allowed_fields: Iterable[PublicField],
) -> dict[str, Any]:
    """Render an explicit allowlist of public fields from a nested receipt.

    The source receipt is scanned first so secret/private-marked fields and
    sentinel-looking values cannot be hidden by simply omitting them from the
    allowlist. Public output is sorted by key at every mapping level and is safe
    to pass through the function repeatedly with the same allowlist.
    """

    if not isinstance(receipt, Mapping):
        raise TypeError("public receipt source must be a mapping")
    fields = tuple(allowed_fields)
    if not fields:
        raise PublicReceiptError("public receipt allowlist is empty")

    _reject_private_source(receipt, path="receipt")
    trie = _allowlist_trie(fields)
    sanitized = _sanitize_allowed(receipt, trie, path="receipt")
    if not isinstance(sanitized, dict):
        raise PublicReceiptError("public receipt did not render to an object")
    _canonical_json(sanitized)
    return sanitized


def private_summary(value: Any, *, redacted_class: str) -> dict[str, int | str]:
    """Return a bounded public summary for known private evidence payloads."""

    if not _OPAQUE_ID_RE.fullmatch(redacted_class):
        raise PublicReceiptError("private summary class must be opaque")
    return {
        "redacted_class": redacted_class,
        "item_count": _count_private_items(value),
    }


def assert_public_receipt_safe(receipt: Mapping[str, Any]) -> None:
    _reject_private_source(receipt, path="receipt")
    _canonical_json(receipt)


def _allowlist_trie(fields: tuple[PublicField, ...]) -> dict[str, Any]:
    root: dict[str, Any] = {}
    for field in fields:
        node = root
        for part in field.path:
            node = node.setdefault(part, {})
        existing = node.get("__field__")
        if existing is not None and existing != field:
            raise PublicReceiptError(f"duplicate public receipt field: {'.'.join(field.path)}")
        node["__field__"] = field
    return root


def _sanitize_allowed(value: Any, trie: Mapping[str, Any], *, path: str) -> Any:
    field = trie.get("__field__")
    if field is not None:
        return _sanitize_leaf(value, field, path=path)

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key in sorted(key for key in trie if key != "__field__"):
            if key in value:
                result[key] = _sanitize_allowed(value[key], trie[key], path=f"{path}.{key}")
        return result

    if isinstance(value, (list, tuple)) and _WILDCARD in trie:
        return [
            _sanitize_allowed(child, trie[_WILDCARD], path=f"{path}[{index}]")
            for index, child in enumerate(value)
        ]

    raise PublicReceiptError(f"{path} does not match public receipt allowlist shape")


def _sanitize_leaf(value: Any, field: PublicField, *, path: str) -> Any:
    kind = field.kind
    if kind == "boolean":
        if not isinstance(value, bool):
            raise PublicReceiptError(f"{path} must be a boolean")
        return value
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PublicReceiptError(f"{path} must be a non-negative integer")
        return value
    if kind == "aggregate":
        return _sanitize_aggregate(value, path=path)
    if not isinstance(value, str):
        raise PublicReceiptError(f"{path} must be a string")
    if len(value) > field.max_length:
        raise PublicReceiptError(f"{path} exceeds public receipt field length")
    _reject_private_string(value, path=path)
    if kind == "hash":
        if _HASH_RE.fullmatch(value) is None:
            raise PublicReceiptError(f"{path} must be a hex hash")
    elif kind == "opaque_id":
        if _OPAQUE_ID_RE.fullmatch(value) is None or _URL_OR_EMAIL_PATTERN.search(value):
            raise PublicReceiptError(f"{path} must be an opaque public id")
    elif kind == "status":
        if _STATUS_RE.fullmatch(value) is None:
            raise PublicReceiptError(f"{path} must be a bounded status code")
    elif kind != "string":
        raise PublicReceiptError(f"{path} has unsupported public field kind")
    return value


def _sanitize_aggregate(value: Any, *, path: str) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in sorted(value.items()):
            if not isinstance(key, str) or not _OPAQUE_ID_RE.fullmatch(key):
                raise PublicReceiptError(f"{path} aggregate key must be opaque")
            result[key] = _sanitize_aggregate(child, path=f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize_aggregate(child, path=f"{path}[]") for child in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str):
        if len(value) > MAX_PUBLIC_STRING_LENGTH:
            raise PublicReceiptError(f"{path} aggregate string is too long")
        _reject_private_string(value, path=path)
        return value
    raise PublicReceiptError(f"{path} aggregate value is not public-safe")


def _reject_private_source(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise PublicReceiptError(f"{path} keys must be strings")
            lowered = key.lower()
            if any(part in lowered for part in _PRIVATE_FIELD_PARTS):
                raise PublicReceiptError(f"{path}.{key} is private-marked")
            _reject_private_source(child, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_private_source(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        _reject_private_string(value, path=path)
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    raise PublicReceiptError(f"{path} contains a non-JSON-safe value")


def _reject_private_string(value: str, *, path: str) -> None:
    if _PRIVATE_PATH_PATTERN.search(value):
        raise PublicReceiptError(f"{path} looks like a private path")
    if any(pattern.search(value) for pattern in _PRIVATE_VALUE_PATTERNS):
        raise PublicReceiptError(f"{path} looks like private material")


def _count_private_items(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, Mapping):
        return len(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return len(value)
    return 1


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True)
