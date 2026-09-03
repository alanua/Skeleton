from __future__ import annotations

import json
from typing import Any, Mapping

from core.local_inference_adapters import AdapterSpec, InferenceValidationError, validate_json_schema

REQUEST_TYPE = "email_message.classify"
RESPONSE_SCHEMA_ID = "skeleton.email_message_inference.v1"
PRIMARY_CATEGORIES = (
    "government", "finance", "work", "security", "technical", "shopping",
    "travel", "education", "personal", "ads", "spam", "other",
)
IMPORTANCE = ("critical", "high", "normal", "low")

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["from_addr", "to_addr", "subject", "snippet", "body_text", "gmail_labels"],
    "additionalProperties": False,
    "properties": {
        "from_addr": {"type": "string", "maxLength": 500},
        "to_addr": {"type": "string", "maxLength": 1000},
        "subject": {"type": "string", "maxLength": 1000},
        "snippet": {"type": "string", "maxLength": 3000},
        "body_text": {"type": "string", "maxLength": 20000},
        "gmail_labels": {"type": "array", "maxItems": 64, "items": {"type": "string", "maxLength": 100}},
    },
}

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema", "route", "primary_category", "importance", "is_marketing",
        "is_spam_suspected", "security_event", "technical_consequence",
        "action_required", "deadline", "summary_uk", "important_points_uk",
        "case_key", "confidence", "evidence", "reason_codes",
    ],
    "additionalProperties": False,
    "properties": {
        "schema": {"type": "string", "const": RESPONSE_SCHEMA_ID},
        "route": {"type": "string", "enum": ["ACCEPT", "REVIEW"]},
        "primary_category": {"type": "string", "enum": list(PRIMARY_CATEGORIES)},
        "importance": {"type": "string", "enum": list(IMPORTANCE)},
        "is_marketing": {"type": "boolean"},
        "is_spam_suspected": {"type": "boolean"},
        "security_event": {"type": "boolean"},
        "technical_consequence": {"type": "boolean"},
        "action_required": {"type": "boolean"},
        "deadline": {"type": ["string", "null"], "maxLength": 32},
        "summary_uk": {"type": "string", "minLength": 1, "maxLength": 900},
        "important_points_uk": {"type": "array", "maxItems": 8, "items": {"type": "string", "minLength": 1, "maxLength": 360}},
        "case_key": {"type": ["string", "null"], "maxLength": 160},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "array", "maxItems": 8, "items": {"type": "string", "minLength": 1, "maxLength": 280}},
        "reason_codes": {"type": "array", "maxItems": 12, "items": {"type": "string", "minLength": 1, "maxLength": 96, "pattern": r"^[A-Z0-9_]+$"}},
    },
}


def build_email_message_prompt(payload: Mapping[str, Any]) -> str:
    validate_json_schema(payload, _INPUT_SCHEMA)
    compact = dict(payload)
    return (
        "You are Skeleton's private semantic email triage component. Return exactly one JSON object and no prose. "
        "Classify the actual intent and consequence of the message, not incidental words. Sender identity, subject and snippet are primary evidence; body text is supporting evidence. "
        "Ignore signatures, legal footers, unsubscribe text, tracking text, quoted replies and generic website security/privacy boilerplate unless they are the actual message. "
        "Choose exactly one primary_category. Security means a real authentication/security event, breach, suspicious access, credential/2FA action, account protection warning or phishing/scam—not merely the words security/login in a footer. "
        "Finance means a real payment, invoice, statement, banking/tax/insurance/contract financial consequence—not a discount, coupon or price mentioned in marketing. "
        "Work means actual employment, client, business, application or professional obligation—not generic commercial mail. Technical means an operational technical report/problem/change; technical_consequence is true only when there is a real system consequence requiring awareness/action. "
        "Ads means promotional/newsletter content. Spam means phishing/scam or strongly suspicious unsolicited content; Gmail SPAM is evidence but still inspect content. Travel price alerts belong to travel, not finance/security. "
        "Set action_required only for a concrete future action. Set deadline only if explicitly supported. Importance high/critical requires a real consequence, deadline, security risk, official/legal matter, money due/received, or important work/personal obligation. "
        "Use REVIEW when confidence < 0.82 or category/consequence is genuinely ambiguous. ACCEPT requires confidence >= 0.82. Do not invent facts. Summaries and important points must be in Ukrainian. "
        f"Use schema {RESPONSE_SCHEMA_ID}. Categories: {json.dumps(PRIMARY_CATEGORIES)}. Input: {json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}"
    )


def validate_email_message_output(value: Mapping[str, Any], request_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    validate_json_schema(request_payload, _INPUT_SCHEMA)
    validate_json_schema(value, _OUTPUT_SCHEMA)
    out = dict(value)
    conf = float(out["confidence"])
    if out["route"] == "ACCEPT" and conf < 0.82:
        raise InferenceValidationError("acceptance_confidence_too_low")
    if out["route"] == "REVIEW" and not out["reason_codes"]:
        raise InferenceValidationError("review_reason_missing")
    if out["primary_category"] == "security" and not (out["security_event"] or out["is_spam_suspected"]):
        raise InferenceValidationError("security_without_security_event")
    if out["primary_category"] == "ads" and not out["is_marketing"]:
        raise InferenceValidationError("ads_without_marketing_signal")
    if out["primary_category"] == "spam" and not out["is_spam_suspected"]:
        raise InferenceValidationError("spam_without_spam_signal")
    if out["deadline"] is not None and not out["action_required"]:
        raise InferenceValidationError("deadline_without_action")
    if not out["evidence"]:
        raise InferenceValidationError("evidence_missing")
    return out


EMAIL_MESSAGE_ADAPTER = AdapterSpec(
    request_type=REQUEST_TYPE,
    prompt_builder=build_email_message_prompt,
    output_validator=validate_email_message_output,
    output_schema=_OUTPUT_SCHEMA,
)
