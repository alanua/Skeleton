from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Final

from core.mail_security import MailSecurityAssessment, MailSecurityCategory, assess_mail_security
from core.scheduler_models import SCHEDULE_SCHEMA, ScheduleSpec
from core.shared_dispatch import PRIVACY_PUBLIC_SAFE


MAIL_OPERATION_RECEIPT_SCHEMA: Final = "skeleton.mail_operations.receipt.v1"
MAIL_OPERATOR_PACKET_SCHEMA: Final = "skeleton.mail_operations.operator_packet.v1"
MAIL_SCHEDULER_CHECKPOINT_SCHEMA: Final = "skeleton.mail_operations.scheduler_checkpoint.v1"
DRAFT_REVISION_SCHEMA: Final = "skeleton.mail_operations.semantic_draft_revision.v1"
MAIL_POLICY_SCHEMA: Final = "skeleton.mail_operations.policy.v1"

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_UA_HINT_RE = re.compile(r"[іїєґІЇЄҐ]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
_IMPORTANT_TERMS = frozenset(
    {
        "important",
        "urgent",
        "deadline",
        "due",
        "action required",
        "asap",
        "терміново",
        "важливо",
        "дедлайн",
        "строк",
    }
)
_INVOICE_TERMS = frozenset({"invoice", "rechnung", "payment due", "заборгованість", "рахунок"})
_TECHNICAL_TERMS = frozenset(
    {"incident", "outage", "error", "failure", "technical", "bug", "збій", "помилка"}
)
_PRIVATE_KEYS = frozenset(
    {
        "body",
        "body_text",
        "html",
        "raw",
        "headers",
        "from",
        "to",
        "cc",
        "bcc",
        "reply_to",
        "subject",
        "attachments",
        "provider_thread_id",
        "provider_message_id",
        "mailbox",
        "email",
        "address",
        "security_metadata",
    }
)
_SECURITY_NO_REPLY_CATEGORIES = frozenset(
    {
        MailSecurityCategory.PHISHING.value,
        MailSecurityCategory.SCAM.value,
        MailSecurityCategory.PSEUDO_INKASSO.value,
        MailSecurityCategory.IDENTITY_MISUSE_SUSPECTED.value,
        MailSecurityCategory.OFFICIAL_LEGAL_NOTICE.value,
    }
)


class MailOperationError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class MailEnvelope:
    provider: str
    provider_message_ref: str
    received_at: int
    subject_hint: str
    body_preview: str
    importance_hint: str | None = None
    deadline_hint: str | None = None
    sender_ref: str | None = None
    thread_ref: str | None = None
    security_metadata: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "MailEnvelope":
        if not isinstance(value, Mapping):
            raise MailOperationError("INVALID_MAIL_ENVELOPE", "mail envelope must be an object")
        provider = _safe_token(value.get("provider"), "provider")
        provider_message_ref = _bounded_text(value.get("provider_message_ref"), "provider_message_ref", 256)
        received_at = _non_negative_int(value.get("received_at"), "received_at")
        subject_hint = _bounded_text(value.get("subject_hint"), "subject_hint", 512)
        body_preview = _bounded_text(value.get("body_preview"), "body_preview", 4096)
        return cls(
            provider=provider,
            provider_message_ref=provider_message_ref,
            received_at=received_at,
            subject_hint=subject_hint,
            body_preview=body_preview,
            importance_hint=_optional_text(value.get("importance_hint"), "importance_hint", 128),
            deadline_hint=_optional_text(value.get("deadline_hint"), "deadline_hint", 256),
            sender_ref=_optional_text(value.get("sender_ref"), "sender_ref", 256),
            thread_ref=_optional_text(value.get("thread_ref"), "thread_ref", 256),
            security_metadata=_optional_mapping(value.get("security_metadata"), "security_metadata"),
        )

    @property
    def local_text(self) -> str:
        return " ".join(part for part in (self.subject_hint, self.body_preview) if part).strip()

    def stable_message_hash(self) -> str:
        return _stable_hash(
            {
                "provider": self.provider,
                "provider_message_ref": self.provider_message_ref,
                "received_at": self.received_at,
                "thread_ref": self.thread_ref,
            }
        )


@dataclass(frozen=True)
class NormalizedCorrespondence:
    case_ref: str
    correspondence_ref: str
    message_hash: str
    source_language: str
    important: bool
    deadline_at: int | None


@dataclass(frozen=True)
class MailPolicyDecision:
    policy_id: str
    category: str
    important: bool
    action: str
    reason: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": MAIL_POLICY_SCHEMA,
            "policy_id": self.policy_id,
            "category": self.category,
            "important": self.important,
            "action": self.action,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SemanticDraftRevision:
    draft_ref: str
    revision: int
    approved_semantic_hash: str
    meaning: Mapping[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": DRAFT_REVISION_SCHEMA,
            "draft_ref": self.draft_ref,
            "revision": self.revision,
            "approved_semantic_hash": self.approved_semantic_hash,
            "meaning": _thaw(self.meaning),
        }


def process_important_mail(
    envelope: Mapping[str, Any],
    *,
    now: int,
    operator_language: str = "uk",
    case_namespace: str = "mail",
) -> dict[str, Any]:
    """Normalize a provider-neutral mail envelope into public-safe operator work."""
    _non_negative_int(now, "now")
    mail = MailEnvelope.from_mapping(envelope)
    normalized = normalize_correspondence(mail, case_namespace=case_namespace)
    policy = classify_mail_policy(mail)
    security = assess_mail_security(
        message_hash=normalized.message_hash,
        case_ref=normalized.case_ref,
        correspondence_ref=normalized.correspondence_ref,
        provider=mail.provider,
        policy_category=policy.category,
        subject_hint=mail.subject_hint,
        body_preview=mail.body_preview,
        metadata=mail.security_metadata,
    )
    if not normalized.important and not security.needs_operator:
        return _public_receipt(
            status="IGNORED",
            reason="MAIL_NOT_IMPORTANT",
            normalized=normalized,
            security_assessment=security.to_mapping(),
            operator_packet=None,
            scheduler_checkpoint=None,
            draft_revision=None,
        )
    if security.category == MailSecurityCategory.SPAM:
        return _public_receipt(
            status="IGNORED",
            reason="ROUTINE_SPAM_SUPPRESSED",
            normalized=normalized,
            security_assessment=security.to_mapping(),
            operator_packet=None,
            scheduler_checkpoint=None,
            draft_revision=None,
        )

    no_reply = security.category.value in _SECURITY_NO_REPLY_CATEGORIES
    draft = None if no_reply else build_semantic_draft_revision(normalized, mail)
    checkpoint = (
        build_scheduler_deadline_checkpoint(normalized, mail, now=now)
        if normalized.deadline_at is not None
        else None
    )
    operator_packet = build_ukrainian_operator_packet(
        normalized,
        mail,
        draft_revision=draft,
        scheduler_checkpoint=checkpoint,
        security_assessment=security,
        operator_language=operator_language,
    )
    return _public_receipt(
        status="NEEDS_OPERATOR",
        reason=(
            "MAIL_SECURITY_REVIEW_REQUIRED"
            if security.category.value in _SECURITY_NO_REPLY_CATEGORIES
            else "IMPORTANT_MAIL_REVIEW_REQUIRED"
        ),
        normalized=normalized,
        security_assessment=security.to_mapping(),
        operator_packet=operator_packet,
        scheduler_checkpoint=checkpoint.to_mapping() if checkpoint is not None else None,
        draft_revision=None if draft is None else draft.to_mapping(),
    )


def normalize_correspondence(
    envelope: MailEnvelope | Mapping[str, Any], *, case_namespace: str = "mail"
) -> NormalizedCorrespondence:
    mail = envelope if isinstance(envelope, MailEnvelope) else MailEnvelope.from_mapping(envelope)
    _safe_token(case_namespace, "case_namespace")
    source_language = detect_language_local(mail.local_text)
    message_hash = mail.stable_message_hash()
    thread_binding = mail.thread_ref or mail.sender_ref or message_hash
    case_digest = _stable_hash({"namespace": case_namespace, "thread": thread_binding})
    correspondence_digest = _stable_hash(
        {"namespace": case_namespace, "message_hash": message_hash}
    )
    policy = classify_mail_policy(mail)
    important = policy.important
    if mail.importance_hint is not None:
        important = important or mail.importance_hint.lower() in {"important", "high", "urgent"}
    deadline_at = extract_deadline_epoch(mail.deadline_hint or mail.local_text)
    return NormalizedCorrespondence(
        case_ref=f"case:{case_digest[:24]}",
        correspondence_ref=f"corr:{correspondence_digest[:24]}",
        message_hash=message_hash,
        source_language=source_language,
        important=important,
        deadline_at=deadline_at,
    )


def classify_mail_policy(envelope: MailEnvelope | Mapping[str, Any]) -> MailPolicyDecision:
    mail = envelope if isinstance(envelope, MailEnvelope) else MailEnvelope.from_mapping(envelope)
    text = mail.local_text.lower()
    if any(term in text for term in _INVOICE_TERMS):
        return MailPolicyDecision(
            policy_id="mail.invoice.v1",
            category="invoice",
            important=True,
            action="operator_review_required",
            reason="INVOICE_MAIL_REVIEW_REQUIRED",
        )
    if any(term in text for term in _TECHNICAL_TERMS):
        return MailPolicyDecision(
            policy_id="mail.technical.v1",
            category="technical",
            important=True,
            action="operator_review_required",
            reason="TECHNICAL_MAIL_REVIEW_REQUIRED",
        )
    if any(term in text for term in _IMPORTANT_TERMS):
        return MailPolicyDecision(
            policy_id="mail.important.v1",
            category="important",
            important=True,
            action="operator_review_required",
            reason="IMPORTANT_MAIL_REVIEW_REQUIRED",
        )
    return MailPolicyDecision(
        policy_id="mail.default.v1",
        category="general",
        important=False,
        action="ignore",
        reason="MAIL_NOT_IMPORTANT",
    )


def detect_language_local(text: str) -> str:
    bounded = _bounded_text(text, "text", 8192)
    lowered = bounded.lower()
    if _UA_HINT_RE.search(bounded) or any(word in lowered for word in ("доброго", "будь ласка", "терміново")):
        return "uk"
    if _CYRILLIC_RE.search(bounded):
        return "unknown-cyrillic"
    if any(word in lowered for word in (" der ", " die ", " und ", " bitte ", "frist")):
        return "de"
    return "en"


def extract_deadline_epoch(text: str) -> int | None:
    bounded = _bounded_text(text, "text", 8192)
    match = _DATE_RE.search(bounded)
    if match is None:
        return None
    try:
        parsed = datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            9,
            0,
            0,
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None
    return int(parsed.timestamp())


def build_scheduler_deadline_checkpoint(
    normalized: NormalizedCorrespondence,
    envelope: MailEnvelope | Mapping[str, Any],
    *,
    now: int,
) -> ScheduleSpec:
    mail = envelope if isinstance(envelope, MailEnvelope) else MailEnvelope.from_mapping(envelope)
    if normalized.deadline_at is None:
        raise MailOperationError("MISSING_DEADLINE", "deadline checkpoint requires deadline_at")
    checkpoint_id = _checkpoint_id(normalized)
    schedule_id = f"mail.deadline.{normalized.correspondence_ref.split(':', 1)[1]}"
    payload = {
        "schema": MAIL_SCHEDULER_CHECKPOINT_SCHEMA,
        "privacy_boundary": PRIVACY_PUBLIC_SAFE,
        "bounded": True,
        "approved_capabilities": ["mail:operator_checkpoint"],
        "requested_capabilities": ["mail:operator_checkpoint"],
        "task_packet": {
            "checkpoint_id": checkpoint_id,
            "case_ref": normalized.case_ref,
            "correspondence_ref": normalized.correspondence_ref,
            "message_hash": normalized.message_hash,
            "deadline_at": normalized.deadline_at,
            "source_provider": mail.provider,
            "action": "operator_follow_up",
        },
        "deterministic_workflow": {"steps": [{"checkpoint_id": checkpoint_id}], "index": 0},
    }
    return ScheduleSpec.from_mapping(
        {
            "schema": SCHEDULE_SCHEMA,
            "schedule_id": schedule_id,
            "trigger_kind": "once",
            "cron_expression": None,
            "once_at": max(_non_negative_int(now, "now"), normalized.deadline_at),
            "timezone": "UTC",
            "route_type": "workflow",
            "route_id": "mail.operator_checkpoint",
            "approval_policy": "require_operator_each_occurrence",
            "overlap_policy": "queue_one",
            "misfire_policy": "needs_operator",
            "payload": payload,
        }
    )


def replay_deadline_checkpoint(
    existing: Sequence[ScheduleSpec], candidate: ScheduleSpec
) -> tuple[ScheduleSpec, ...]:
    """Return exactly one checkpoint for repeated extraction of the same deadline."""
    by_id = {item.schedule_id: item for item in existing}
    by_id.setdefault(candidate.schedule_id, candidate)
    return tuple(by_id[key] for key in sorted(by_id))


def build_semantic_draft_revision(
    normalized: NormalizedCorrespondence,
    envelope: MailEnvelope | Mapping[str, Any],
    *,
    revision: int = 1,
) -> SemanticDraftRevision:
    mail = envelope if isinstance(envelope, MailEnvelope) else MailEnvelope.from_mapping(envelope)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
        raise MailOperationError("INVALID_DRAFT_REVISION", "revision must be positive")
    meaning = {
        "case_ref": normalized.case_ref,
        "correspondence_ref": normalized.correspondence_ref,
        "intent": "important_mail_operator_reply",
        "deadline_at": normalized.deadline_at,
        "source_language": normalized.source_language,
        "requested_outcome": "operator_reviews_and_approves_reply",
        "summary_fingerprint": _stable_hash(mail.local_text)[:24],
    }
    approved_hash = _stable_hash(meaning)
    return SemanticDraftRevision(
        draft_ref=f"draft:{approved_hash[:24]}",
        revision=revision,
        approved_semantic_hash=approved_hash,
        meaning=meaning,
    )


def render_draft_in_source_language(
    draft_revision: SemanticDraftRevision | Mapping[str, Any],
    *,
    source_language: str,
    approved_semantic_hash: str | None = None,
) -> dict[str, Any]:
    draft = _draft_from_mapping(draft_revision) if isinstance(draft_revision, Mapping) else draft_revision
    expected = approved_semantic_hash or draft.approved_semantic_hash
    if expected != draft.approved_semantic_hash:
        raise MailOperationError(
            "SEMANTIC_DRAFT_HASH_MISMATCH",
            "approved semantic meaning changed before source-language render",
        )
    body = {
        "uk": "Дякую. Я перевірю важливий лист і повернуся з відповіддю.",
        "de": "Danke. Ich pruefe die wichtige Nachricht und melde mich mit einer Antwort.",
        "en": "Thank you. I will review the important message and follow up with a reply.",
    }.get(source_language, "Thank you. I will review the important message and follow up.")
    return {
        "draft_ref": draft.draft_ref,
        "revision": draft.revision,
        "approved_semantic_hash": draft.approved_semantic_hash,
        "render_language": source_language,
        "body": body,
        "semantic_hash_verified": True,
    }


def build_ukrainian_operator_packet(
    normalized: NormalizedCorrespondence,
    envelope: MailEnvelope | Mapping[str, Any],
    *,
    draft_revision: SemanticDraftRevision | None,
    scheduler_checkpoint: ScheduleSpec | None,
    security_assessment: MailSecurityAssessment | Mapping[str, Any] | None = None,
    operator_language: str = "uk",
) -> dict[str, Any]:
    mail = envelope if isinstance(envelope, MailEnvelope) else MailEnvelope.from_mapping(envelope)
    if operator_language != "uk":
        raise MailOperationError("UNSUPPORTED_OPERATOR_LANGUAGE", "operator presentation must be Ukrainian")
    security_mapping = _security_mapping(security_assessment)
    security_category = security_mapping.get("category")
    no_reply = security_category in _SECURITY_NO_REPLY_CATEGORIES
    if no_reply:
        actions = [
            {"id": "open_private_case", "label_uk": "Відкрити приватну справу"},
            {"id": "search_private_evidence", "label_uk": "Перевірити докази"},
            {"id": "defer", "label_uk": "Відкласти"},
            {"id": "mark_false_positive", "label_uk": "Позначити як хибний сигнал"},
        ]
    else:
        if draft_revision is None:
            raise MailOperationError("MISSING_DRAFT_REVISION", "operator reply packet requires draft revision")
        actions = [
            {
                "id": "approve_reply",
                "label_uk": "Схвалити відповідь",
                "requires_semantic_hash": draft_revision.approved_semantic_hash,
            },
            {"id": "revise_reply", "label_uk": "Уточнити відповідь"},
            {"id": "defer", "label_uk": "Відкласти"},
        ]
    if scheduler_checkpoint is not None:
        actions.append({"id": "confirm_deadline", "label_uk": "Підтвердити дедлайн"})
    summary = _ukrainian_summary(normalized, mail)
    policy = classify_mail_policy(mail)
    return {
        "schema": MAIL_OPERATOR_PACKET_SCHEMA,
        "language": "uk",
        "case_ref": normalized.case_ref,
        "correspondence_ref": normalized.correspondence_ref,
        "source_language": normalized.source_language,
        "summary_uk": summary,
        "policy": policy.to_mapping(),
        "security_assessment": security_mapping or None,
        "explanation_uk": (
            "Лист потребує перевірки без відповіді, оплати, переходу за посиланнями або визнання боргу."
            if no_reply
            else "Лист позначено як важливий локальними правилами. Жодних живих читань або відправок не виконано."
        ),
        "telegram_reply_contract": {
            "actionable": not bool(security_mapping.get("suppress_telegram")),
            "allowed_actions": actions,
            "requires_callback": True,
            "public_safe": True,
        },
        "draft_ref": None if draft_revision is None else draft_revision.draft_ref,
        "approved_semantic_hash": None if draft_revision is None else draft_revision.approved_semantic_hash,
        "scheduler_checkpoint_id": (
            _checkpoint_id(normalized) if scheduler_checkpoint is not None else None
        ),
    }


def public_mail_operation_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return _redact(receipt)


def _public_receipt(
    *,
    status: str,
    reason: str,
    normalized: NormalizedCorrespondence,
    security_assessment: Mapping[str, Any] | None,
    operator_packet: Mapping[str, Any] | None,
    scheduler_checkpoint: Mapping[str, Any] | None,
    draft_revision: Mapping[str, Any] | None,
) -> dict[str, Any]:
    receipt = {
        "schema": MAIL_OPERATION_RECEIPT_SCHEMA,
        "status": status,
        "reason": reason,
        "case_ref": normalized.case_ref,
        "correspondence_ref": normalized.correspondence_ref,
        "message_hash": normalized.message_hash,
        "source_language": normalized.source_language,
        "important": normalized.important,
        "deadline_at": normalized.deadline_at,
        "security_assessment": security_assessment,
        "operator_packet": operator_packet,
        "scheduler_checkpoint": scheduler_checkpoint,
        "draft_revision": draft_revision,
        "public_safe": True,
        "private_payloads_included": False,
        "external_side_effects_executed": False,
    }
    return _redact(receipt)


def _ukrainian_summary(normalized: NormalizedCorrespondence, mail: MailEnvelope) -> str:
    deadline = (
        datetime.fromtimestamp(normalized.deadline_at, tz=timezone.utc).strftime("%Y-%m-%d")
        if normalized.deadline_at is not None
        else "не виявлено"
    )
    topic = _compact_topic(mail.subject_hint or mail.body_preview)
    return f"Важлива кореспонденція: {topic}. Дедлайн: {deadline}."


def _compact_topic(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= 96:
        return normalized
    return normalized[:93].rstrip() + "..."


def _checkpoint_id(normalized: NormalizedCorrespondence) -> str:
    return f"mail_checkpoint:{_stable_hash({'corr': normalized.correspondence_ref, 'deadline_at': normalized.deadline_at})[:24]}"


def _draft_from_mapping(value: Mapping[str, Any]) -> SemanticDraftRevision:
    if value.get("schema") != DRAFT_REVISION_SCHEMA:
        raise MailOperationError("INVALID_DRAFT_SCHEMA", "invalid draft revision schema")
    draft_ref = _bounded_text(value.get("draft_ref"), "draft_ref", 128)
    revision = _non_negative_int(value.get("revision"), "revision")
    if revision == 0:
        raise MailOperationError("INVALID_DRAFT_REVISION", "revision must be positive")
    approved_hash = _hash(value.get("approved_semantic_hash"), "approved_semantic_hash")
    meaning = value.get("meaning")
    if not isinstance(meaning, Mapping):
        raise MailOperationError("INVALID_DRAFT_MEANING", "draft meaning must be an object")
    return SemanticDraftRevision(
        draft_ref=draft_ref,
        revision=revision,
        approved_semantic_hash=approved_hash,
        meaning=dict(meaning),
    )


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in _PRIVATE_KEYS:
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        _thaw(value), ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def _safe_token(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None:
        raise MailOperationError("INVALID_TOKEN", f"{field} must be a safe token")
    return value


def _bounded_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise MailOperationError("INVALID_TEXT", f"{field} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > limit:
        raise MailOperationError("INVALID_TEXT", f"{field} must be non-empty and bounded")
    return normalized


def _optional_text(value: Any, field: str, limit: int) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, field, limit)


def _optional_mapping(value: Any, field: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise MailOperationError("INVALID_MAPPING", f"{field} must be an object")
    return dict(value)


def _non_negative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MailOperationError("INVALID_INTEGER", f"{field} must be a non-negative integer")
    return value


def _security_mapping(value: MailSecurityAssessment | Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, MailSecurityAssessment):
        return value.to_mapping()
    return dict(value)


def _hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise MailOperationError("INVALID_HASH", f"{field} must be a sha256 hash")
    return value
