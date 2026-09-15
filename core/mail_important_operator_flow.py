from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping

from core.scheduler_models import (
    ScheduleSpec,
    build_execution_proposal,
    stable_occurrence_id,
    thaw_json,
)
from core.scheduler_store import SchedulerStore


FLOW_SCHEMA = "skeleton.mail_important_operator_flow.result.v1"
CASE_SCHEMA = "skeleton.mail_important.case.v1"
CORRESPONDENCE_SCHEMA = "skeleton.mail_important.correspondence.v1"
TELEGRAM_SCHEMA = "skeleton.mail_important.telegram_contract.v1"
CALLBACK_SCHEMA = "skeleton.mail_important.telegram_callback.v1"
DRAFT_SCHEMA = "skeleton.mail_important.semantic_draft.v1"

_DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_TOKEN_RE = re.compile(r"[^a-z0-9._:-]+")
_PRIVATE_MARKER_RE = re.compile(r"SYNTH_[A-Z0-9_:-]+|PRIVATE_MARKER_[A-Z0-9_:-]+")


def process_important_mail_event(
    event: Mapping[str, object],
    *,
    scheduler_store: SchedulerStore,
    now: int,
) -> dict[str, object]:
    """Normalize one synthetic important-mail event for operator presentation.

    The flow is provider-neutral by construction: it accepts a caller-supplied
    fixture only, never reads mail providers, and returns public-safe receipts
    that omit source body, addresses, attachments, and synthetic private markers.
    """
    _require_synthetic_fixture(event)
    _timestamp(now, "now")
    scheduler_store.initialize()

    event_id = _required_text(event, "event_id")
    subject = _required_text(event, "subject")
    body_text = _required_text(event, "body_text")
    received_at = _parse_utc_timestamp(_required_text(event, "received_at"), "received_at")
    sender_label = _public_sender_label(event.get("from_name"))
    importance = str(event.get("importance", "important"))

    case_ref = f"case_mail_{_digest(event_id)[:16]}"
    correspondence_ref = f"corr_{_digest(event_id + ':correspondence')[:16]}"
    language = _detect_language(subject, body_text)
    deadline = _extract_deadline(event, body_text)
    explanation = _build_operator_explanation(
        subject=subject,
        body_text=body_text,
        deadline=deadline,
        language_code=str(language["language_code"]),
        importance=importance,
    )
    semantic_draft = _semantic_draft(case_ref, correspondence_ref, explanation, deadline)
    checkpoints = _create_deadline_checkpoints(
        scheduler_store=scheduler_store,
        case_ref=case_ref,
        correspondence_ref=correspondence_ref,
        deadline=deadline,
        now=now,
    )
    correspondence = {
        "schema": CORRESPONDENCE_SCHEMA,
        "correspondence_ref": correspondence_ref,
        "case_ref": case_ref,
        "channel": "mail",
        "provider_neutral": True,
        "received_at": _format_utc(received_at),
        "sender_label": sender_label,
        "subject": _redact_markers(subject),
        "source_language": language,
        "body_private_gated": True,
        "body_included": False,
        "attachments_included": False,
        "semantic_draft_revision": semantic_draft["revision"],
        "semantic_draft_hash": semantic_draft["semantic_hash"],
    }
    case = {
        "schema": CASE_SCHEMA,
        "case_ref": case_ref,
        "status": "operator_review",
        "importance": "important",
        "correspondence_refs": [correspondence_ref],
        "deadline": deadline,
        "risk": explanation["risk"],
        "private_context_gated": True,
    }
    telegram = _telegram_contract(
        case=case,
        correspondence=correspondence,
        explanation=explanation,
        semantic_draft=semantic_draft,
    )
    public_receipt = {
        "schema": "skeleton.mail_important.public_receipt.v1",
        "case_ref": case_ref,
        "correspondence_ref": correspondence_ref,
        "deadline_checkpoint_count": len(checkpoints),
        "scheduler_checkpoints": checkpoints,
        "public_safe": True,
        "private_payloads_included": False,
        "external_side_effects_executed": False,
    }
    _assert_public_safe(public_receipt, event)

    return {
        "schema": FLOW_SCHEMA,
        "status": "DONE",
        "case": case,
        "correspondence": correspondence,
        "operator_language": {
            "target_language": "uk",
            "detected_source_language": language,
            "translation_uk": explanation["translation_uk"],
            "summary_uk": explanation["summary_uk"],
            "explanation_uk": explanation["explanation_uk"],
        },
        "telegram_contract": telegram,
        "semantic_draft": semantic_draft,
        "scheduler_checkpoints": checkpoints,
        "policy_metadata": _policy_metadata(language, explanation),
        "public_receipt": public_receipt,
    }


def render_source_language_reply_prompt(
    flow_result: Mapping[str, object], *, source_language: str
) -> dict[str, object]:
    """Prepare a later render stage while preserving approved draft semantics."""
    semantic_draft = flow_result.get("semantic_draft")
    if not isinstance(semantic_draft, Mapping):
        raise ValueError("flow_result semantic_draft must be an object")
    return {
        "schema": "skeleton.mail_important.source_language_render.v1",
        "source_language": source_language,
        "draft_semantic_revision": semantic_draft["revision"],
        "draft_semantic_hash": semantic_draft["semantic_hash"],
        "meaning_locked": True,
        "requires_reapproval_if_semantic_hash_changes": True,
    }


def _create_deadline_checkpoints(
    *,
    scheduler_store: SchedulerStore,
    case_ref: str,
    correspondence_ref: str,
    deadline: Mapping[str, object] | None,
    now: int,
) -> list[dict[str, object]]:
    if deadline is None:
        return []
    due_at = deadline.get("due_at")
    if not isinstance(due_at, int):
        return []
    schedule_id = _safe_id(f"mail.deadline.{case_ref}")
    spec = ScheduleSpec.from_mapping(
        {
            "schema": "skeleton.schedule.v1",
            "schedule_id": schedule_id,
            "trigger_kind": "once",
            "cron_expression": None,
            "once_at": due_at,
            "timezone": "UTC",
            "route_type": "notify",
            "route_id": "mail.important_deadline",
            "approval_policy": "require_operator_each_occurrence",
            "overlap_policy": "skip",
            "misfire_policy": "run_once",
            "payload": {
                "schema": "skeleton.mail_important.scheduler_payload.v1",
                "case_ref": case_ref,
                "correspondence_ref": correspondence_ref,
                "deadline_kind": deadline["kind"],
                "public_safe": True,
            },
        }
    )
    schedule, schedule_created = scheduler_store.register(spec, now=now, enabled=True)
    occurrence_id = stable_occurrence_id(schedule.spec.schedule_id, schedule.version, due_at)
    proposal = build_execution_proposal(
        schedule, occurrence_id=occurrence_id, scheduled_for=due_at
    )
    occurrence, occurrence_created = scheduler_store.create_occurrence(
        occurrence_id=occurrence_id,
        schedule=schedule,
        scheduled_for=due_at,
        state="needs_operator",
        reason="MAIL_DEADLINE_CHECKPOINT",
        proposal=proposal,
        now=now,
    )
    return [
        {
            "schedule_id": schedule.spec.schedule_id,
            "schedule_created": schedule_created,
            "occurrence_created": occurrence_created,
            "occurrence": occurrence.public_receipt(),
        }
    ]


def _telegram_contract(
    *,
    case: Mapping[str, object],
    correspondence: Mapping[str, object],
    explanation: Mapping[str, object],
    semantic_draft: Mapping[str, object],
) -> dict[str, object]:
    case_ref = str(case["case_ref"])
    correspondence_ref = str(correspondence["correspondence_ref"])
    return {
        "schema": TELEGRAM_SCHEMA,
        "case_ref": case_ref,
        "correspondence_ref": correspondence_ref,
        "title_uk": "Важливий лист потребує відповіді",
        "summary_uk": explanation["summary_uk"],
        "explanation_uk": explanation["explanation_uk"],
        "deadline_uk": explanation["deadline_uk"],
        "risk_uk": explanation["risk_uk"],
        "private_context_available": False,
        "actions": [
            {
                "label": "Підготувати відповідь",
                "callback": {
                    "schema": CALLBACK_SCHEMA,
                    "action": "prepare_reply",
                    "case_ref": case_ref,
                    "correspondence_ref": correspondence_ref,
                    "draft_semantic_revision": semantic_draft["revision"],
                    "draft_semantic_hash": semantic_draft["semantic_hash"],
                },
            }
        ],
    }


def _semantic_draft(
    case_ref: str,
    correspondence_ref: str,
    explanation: Mapping[str, object],
    deadline: Mapping[str, object] | None,
) -> dict[str, object]:
    semantics = {
        "case_ref": case_ref,
        "correspondence_ref": correspondence_ref,
        "meaning_uk": explanation["explanation_uk"]["meaning"],
        "request_uk": explanation["explanation_uk"]["request"],
        "deadline": deadline,
        "risk": explanation["risk"],
        "reply_intent": "acknowledge_request_and_prepare_answer",
    }
    return {
        "schema": DRAFT_SCHEMA,
        "revision": 1,
        "semantics": semantics,
        "semantic_hash": _hash_json(semantics),
    }


def _build_operator_explanation(
    *,
    subject: str,
    body_text: str,
    deadline: Mapping[str, object] | None,
    language_code: str,
    importance: str,
) -> dict[str, object]:
    lowered = f"{subject}\n{body_text}".lower()
    high_risk = any(token in lowered for token in ("legal", "breach", "urgent", "терміново"))
    request = _request_uk(lowered)
    deadline_uk = (
        f"Строк: {_format_deadline_uk(deadline)}."
        if deadline is not None
        else "Явний строк не знайдено."
    )
    risk_label = "high" if high_risk else "normal"
    risk_uk = (
        "Високий ризик: лист позначено як терміновий або юридично значущий."
        if high_risk
        else "Ризик звичайний: достатньо підготувати відповідь без негайної ескалації."
    )
    summary = (
        f"{_subject_uk(subject, language_code)} Відправник просить: {request}. {deadline_uk}"
    )
    return {
        "translation_uk": _translation_uk(subject, body_text, language_code),
        "summary_uk": summary,
        "explanation_uk": {
            "meaning": "Це важлива людська кореспонденція, яку треба розглянути оператору.",
            "request": request,
            "deadline": deadline_uk,
            "risk": risk_uk,
        },
        "deadline_uk": deadline_uk,
        "risk_uk": risk_uk,
        "risk": {
            "level": risk_label,
            "importance": importance,
            "operator_review_required": high_risk,
        },
    }


def _translation_uk(subject: str, body_text: str, language_code: str) -> str:
    if language_code == "uk":
        return _redact_markers(f"{subject}. {body_text}")
    return (
        f"Тема: {_redact_markers(subject)}. Зміст: важливий лист просить "
        f"підготувати відповідь. Ключові деталі збережено у приватному контексті."
    )


def _subject_uk(subject: str, language_code: str) -> str:
    if language_code == "uk":
        return f"Тема: {_redact_markers(subject)}."
    return f"Тема листа: {_redact_markers(subject)}."


def _request_uk(lowered_text: str) -> str:
    if "confirm" in lowered_text or "підтверд" in lowered_text:
        return "підтвердити отримання та надати відповідь"
    if "send" in lowered_text or "надіш" in lowered_text:
        return "надіслати потрібну інформацію у відповіді"
    if "review" in lowered_text or "перевір" in lowered_text:
        return "перевірити матеріали та відповісти"
    return "підготувати змістовну відповідь"


def _extract_deadline(
    event: Mapping[str, object], body_text: str
) -> dict[str, object] | None:
    explicit = event.get("deadline_at")
    if isinstance(explicit, str) and explicit:
        due = _parse_utc_timestamp(explicit, "deadline_at")
        return {"kind": "explicit", "due_at": int(due.timestamp()), "source": "fixture_field"}
    match = _DATE_RE.search(body_text)
    if match is None:
        return None
    parsed = datetime(
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        9,
        0,
        tzinfo=timezone.utc,
    )
    return {"kind": "date_in_body", "due_at": int(parsed.timestamp()), "source": "body_date"}


def _detect_language(subject: str, body_text: str) -> dict[str, object]:
    text = _redact_markers(f"{subject}\n{body_text}")
    lowered = text.lower()
    if re.search(r"[іїєґІЇЄҐ]", text):
        return {"language_code": "uk", "confidence": 0.98, "method": "local_heuristic"}
    if any(word in lowered for word in ("bitte", "frist", "antwort", "danke")):
        return {"language_code": "de", "confidence": 0.86, "method": "local_heuristic"}
    ascii_letters = sum(1 for char in text if "a" <= char.lower() <= "z")
    if ascii_letters >= 20:
        return {"language_code": "en", "confidence": 0.91, "method": "local_heuristic"}
    return {"language_code": "unknown", "confidence": 0.42, "method": "local_heuristic"}


def _policy_metadata(
    language: Mapping[str, object], explanation: Mapping[str, object]
) -> dict[str, object]:
    confidence = float(language["confidence"])
    risk = explanation["risk"]
    high_risk = isinstance(risk, Mapping) and risk.get("level") == "high"
    return {
        "escalation": {
            "required": confidence < 0.7 or high_risk,
            "reasons": [
                reason
                for reason, enabled in (
                    ("low_language_confidence", confidence < 0.7),
                    ("high_correspondence_risk", high_risk),
                )
                if enabled
            ],
            "metadata_only": True,
            "private_context_gated": True,
        }
    }


def _require_synthetic_fixture(event: Mapping[str, object]) -> None:
    if not isinstance(event, Mapping):
        raise ValueError("event must be an object")
    if event.get("synthetic_fixture") is not True:
        raise ValueError("important mail flow accepts synthetic fixtures only")
    provider = event.get("provider")
    if not isinstance(provider, str) or not provider.startswith("synthetic"):
        raise ValueError("provider must be a synthetic provider identifier")


def _required_text(event: Mapping[str, object], field_name: str) -> str:
    value = event.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")
    return value.strip()


def _parse_utc_timestamp(value: str, field_name: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include timezone")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_deadline_uk(deadline: Mapping[str, object] | None) -> str:
    if deadline is None:
        return "не визначено"
    due_at = deadline.get("due_at")
    if not isinstance(due_at, int):
        return "не визначено"
    return datetime.fromtimestamp(due_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _public_sender_label(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return _redact_markers(value.strip())[:80]
    return "sender"


def _redact_markers(value: str) -> str:
    return _PRIVATE_MARKER_RE.sub("[redacted]", value)


def _assert_public_safe(public_receipt: Mapping[str, object], event: Mapping[str, object]) -> None:
    encoded = json.dumps(public_receipt, ensure_ascii=False, sort_keys=True)
    for value in event.values():
        if isinstance(value, str) and _PRIVATE_MARKER_RE.search(value):
            for marker in _PRIVATE_MARKER_RE.findall(value):
                if marker in encoded:
                    raise ValueError("public receipt includes a synthetic private marker")


def _safe_id(value: str) -> str:
    normalized = _TOKEN_RE.sub(".", value.lower()).strip(".")
    return normalized[:120]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        thaw_json(value), ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _timestamp(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value
