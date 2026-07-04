from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

TASK_ENVELOPE_MODE = "TASK_ENVELOPE"
_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class TaskEnvelopeQueueError(ValueError):
    pass


@dataclass(frozen=True)
class QueueEnvelopeRequest:
    issue_number: int
    reference_id: str
    content_hash: str


def parse_queue_request(
    issue: Mapping[str, Any],
    *,
    trusted_authors: frozenset[str],
) -> QueueEnvelopeRequest | None:
    body = issue.get("body")
    number = issue.get("number")
    author = issue.get("author")
    author_login = author.get("login") if isinstance(author, Mapping) else None
    if not isinstance(body, str) or not isinstance(number, int):
        return None
    if not isinstance(author_login, str):
        return None
    if author_login.lower() not in {value.lower() for value in trusted_authors}:
        raise TaskEnvelopeQueueError("queue issue author is not trusted")
    if _field(body, "Mode") != TASK_ENVELOPE_MODE:
        return None

    reference_id = _field(body, "Envelope Ref")
    content_hash = _field(body, "Envelope SHA256")
    if reference_id is None or _REFERENCE_RE.fullmatch(reference_id) is None:
        raise TaskEnvelopeQueueError("envelope reference is invalid")
    if content_hash is None or _HASH_RE.fullmatch(content_hash) is None:
        raise TaskEnvelopeQueueError("envelope hash is invalid")
    return QueueEnvelopeRequest(
        issue_number=number,
        reference_id=reference_id,
        content_hash=content_hash,
    )


def _field(body: str, name: str) -> str | None:
    match = re.search(
        rf"(?mi)^\s*{re.escape(name)}\s*:\s*(?P<value>[^\r\n]+?)\s*$",
        body,
    )
    return match.group("value").strip() if match is not None else None
