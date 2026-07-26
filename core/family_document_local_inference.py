from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Protocol

from core.local_inference_adapters import (
    AdapterSpec,
    InferenceValidationError,
    validate_json_schema,
)

REQUEST_TYPE = "family_document.classify"
RESPONSE_SCHEMA_ID = "skeleton.family_document_inference.v1"
TOPIC_ALIASES = (
    "01 identity_and_civil_status",
    "02 migration_and_residence",
    "03 health_and_insurance",
    "04 work_tax_and_business",
    "05 education_and_qualification",
    "06 finance_banking_and_contracts",
    "07 legal_courts_official_correspondence",
    "08 housing_and_utilities",
    "09 transport_and_travel",
)
EVENT_TYPES = (
    "appointment",
    "hearing",
    "deadline",
    "expiration_renewal",
    "contract_boundary",
    "employment_boundary",
    "insurance_boundary",
    "booked_travel",
    "birthday",
)

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["ocr_text", "allowed_subject_aliases", "source_kind"],
    "additionalProperties": False,
    "properties": {
        "ocr_text": {"type": "string", "minLength": 1, "maxLength": 24000},
        "allowed_subject_aliases": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {"type": "string", "minLength": 1, "maxLength": 80},
        },
        "languages": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 2, "maxLength": 16},
        },
        "source_kind": {"type": "string", "enum": ["mfp", "local_file", "drive"]},
        "page_count": {"type": "integer", "minimum": 1, "maximum": 500},
        "mime_type": {"type": "string", "minLength": 1, "maxLength": 120},
    },
}

_CONFIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "overall",
        "owner",
        "topic",
        "jurisdiction",
        "date",
        "document_type",
        "issuer",
    ],
    "additionalProperties": False,
    "properties": {
        key: {"type": "number", "minimum": 0, "maximum": 1}
        for key in (
            "overall",
            "owner",
            "topic",
            "jurisdiction",
            "date",
            "document_type",
            "issuer",
        )
    },
}

_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["owner", "topic", "jurisdiction", "date", "document_type", "issuer"],
    "additionalProperties": False,
    "properties": {
        key: {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "string", "minLength": 1, "maxLength": 240},
        }
        for key in ("owner", "topic", "jurisdiction", "date", "document_type", "issuer")
    },
}

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema",
        "route",
        "principal_subject_alias",
        "linked_subject_aliases",
        "topic_alias",
        "jurisdiction_country",
        "document_date",
        "date_precision",
        "document_type",
        "issuer",
        "summary",
        "confidence",
        "evidence",
        "event_candidates",
        "reason_codes",
    ],
    "additionalProperties": False,
    "properties": {
        "schema": {"type": "string", "const": RESPONSE_SCHEMA_ID},
        "route": {"type": "string", "enum": ["ACCEPT", "REVIEW"]},
        "principal_subject_alias": {"type": ["string", "null"], "maxLength": 80},
        "linked_subject_aliases": {
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string", "minLength": 1, "maxLength": 80},
        },
        "topic_alias": {"type": ["string", "null"], "enum": [*TOPIC_ALIASES, None]},
        "jurisdiction_country": {"type": ["string", "null"], "maxLength": 80},
        "document_date": {"type": ["string", "null"], "maxLength": 10},
        "date_precision": {"type": "string", "enum": ["day", "month", "year", "unknown"]},
        "document_type": {"type": ["string", "null"], "maxLength": 160},
        "issuer": {"type": ["string", "null"], "maxLength": 160},
        "summary": {"type": "string", "minLength": 1, "maxLength": 1200},
        "confidence": _CONFIDENCE_SCHEMA,
        "evidence": _EVIDENCE_SCHEMA,
        "event_candidates": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "required": ["event_type", "date", "title", "confidence", "evidence"],
                "additionalProperties": False,
                "properties": {
                    "event_type": {"type": "string", "enum": list(EVENT_TYPES)},
                    "date": {"type": "string", "minLength": 4, "maxLength": 10},
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "string", "minLength": 1, "maxLength": 240},
                },
            },
        },
        "reason_codes": {
            "type": "array",
            "maxItems": 12,
            "items": {"type": "string", "minLength": 1, "maxLength": 96, "pattern": r"^[A-Z0-9_]+$"},
        },
    },
}


def build_family_document_prompt(payload: Mapping[str, Any]) -> str:
    validate_json_schema(payload, _INPUT_SCHEMA)
    aliases = list(payload["allowed_subject_aliases"])
    if len(set(aliases)) != len(aliases):
        raise InferenceValidationError("subject_aliases_not_unique")
    compact_payload = {
        "allowed_subject_aliases": aliases,
        "languages": payload.get("languages", []),
        "source_kind": payload["source_kind"],
        "page_count": payload.get("page_count"),
        "mime_type": payload.get("mime_type"),
        "ocr_text": payload["ocr_text"],
    }
    return (
        "You are a local private document-analysis component inside Skeleton. "
        "Return exactly one JSON object and no prose. Do not invent missing facts. "
        "Use only an alias from allowed_subject_aliases. If owner, topic, jurisdiction, "
        "document type or issuer is uncertain, route REVIEW. ACCEPT requires overall "
        "confidence >= 0.80 and evidence for each required field. Country means issuing "
        "or procedure jurisdiction, not residence or nationality. Document date means "
        "the issue/decision/document date, never upload or file timestamp. Emit calendar "
        "candidates only for appointments, hearings, deadlines, expirations/renewals, "
        "contract/employment/insurance boundaries, booked travel or confirmed birthdays. "
        "Never emit paths, filenames, commands, SQL, tool calls or side-effect requests. "
        f"Use schema {RESPONSE_SCHEMA_ID}. Fixed topic aliases: {json.dumps(TOPIC_ALIASES)}. "
        f"Input: {json.dumps(compact_payload, ensure_ascii=False, separators=(',', ':'))}"
    )


def validate_family_document_output(
    value: Mapping[str, Any], request_payload: Mapping[str, Any]
) -> Mapping[str, Any]:
    validate_json_schema(value, _OUTPUT_SCHEMA)
    validate_json_schema(request_payload, _INPUT_SCHEMA)
    normalized = dict(value)
    route = normalized["route"]
    allowed_aliases = set(request_payload["allowed_subject_aliases"])
    aliases = list(normalized["linked_subject_aliases"])
    principal = normalized["principal_subject_alias"]

    if route == "REVIEW":
        aliases = list(dict.fromkeys(alias for alias in aliases if alias in allowed_aliases))
        if principal not in allowed_aliases:
            principal = None
        if principal is not None and principal not in aliases:
            aliases.append(principal)
        normalized["principal_subject_alias"] = principal
        normalized["linked_subject_aliases"] = aliases
    else:
        if len(set(aliases)) != len(aliases):
            raise InferenceValidationError("linked_subject_aliases_not_unique")
        if any(alias not in allowed_aliases for alias in aliases):
            raise InferenceValidationError("linked_subject_alias_not_allowed")
        if principal is not None and principal not in allowed_aliases:
            raise InferenceValidationError("principal_subject_not_allowed")
        if principal is not None and principal not in aliases:
            raise InferenceValidationError("principal_subject_not_linked")

    confidence = normalized["confidence"]
    assert isinstance(confidence, Mapping)
    evidence = normalized["evidence"]
    assert isinstance(evidence, Mapping)
    required_values = (
        principal,
        normalized["topic_alias"],
        normalized["jurisdiction_country"],
        normalized["document_type"],
        normalized["issuer"],
    )
    required_evidence = all(bool(evidence[key]) for key in evidence)
    if route == "ACCEPT" and (
        any(item is None or item == "" for item in required_values)
        or float(confidence["overall"]) < 0.80
        or not required_evidence
    ):
        raise InferenceValidationError("acceptance_contract_not_met")
    if route == "REVIEW" and not normalized["reason_codes"]:
        raise InferenceValidationError("review_reason_missing")
    _validate_date_precision(normalized["document_date"], normalized["date_precision"])
    for candidate in normalized["event_candidates"]:
        _validate_partial_date(candidate["date"])
    return normalized


def _validate_date_precision(raw: object, precision: object) -> None:
    if precision == "unknown":
        if raw is not None:
            raise InferenceValidationError("document_date_precision_invalid")
        return
    if not isinstance(raw, str):
        raise InferenceValidationError("document_date_missing")
    if precision == "day":
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) is None:
            raise InferenceValidationError("document_date_invalid")
        try:
            date.fromisoformat(raw)
        except ValueError as exc:
            raise InferenceValidationError("document_date_invalid") from exc
    elif precision == "month":
        if re.fullmatch(r"\d{4}-\d{2}", raw) is None or not 1 <= int(raw[5:7]) <= 12:
            raise InferenceValidationError("document_date_invalid")
    elif precision == "year":
        if re.fullmatch(r"\d{4}", raw) is None:
            raise InferenceValidationError("document_date_invalid")


def _validate_partial_date(raw: object) -> None:
    if not isinstance(raw, str):
        raise InferenceValidationError("event_date_invalid")
    if re.fullmatch(r"\d{4}", raw):
        return
    if re.fullmatch(r"\d{4}-\d{2}", raw):
        if 1 <= int(raw[5:7]) <= 12:
            return
        raise InferenceValidationError("event_date_invalid")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            date.fromisoformat(raw)
            return
        except ValueError as exc:
            raise InferenceValidationError("event_date_invalid") from exc
    raise InferenceValidationError("event_date_invalid")


def load_exact_subject_aliases(path: str | Path) -> tuple[str, str, str]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InferenceValidationError("family_subject_aliases_invalid") from exc
    if not isinstance(value, list) or len(value) != 3 or any(not isinstance(item, str) for item in value):
        raise InferenceValidationError("family_subject_aliases_invalid")
    aliases = tuple(item.strip() for item in value)
    if any(not item for item in aliases) or len(set(aliases)) != 3:
        raise InferenceValidationError("family_subject_aliases_invalid")
    return aliases  # type: ignore[return-value]


def bind_family_subject_aliases(
    payload: Mapping[str, Any], aliases: tuple[str, str, str]
) -> dict[str, Any]:
    if "allowed_subject_aliases" in payload:
        raise InferenceValidationError("subject_alias_override_forbidden")
    bound = {**dict(payload), "allowed_subject_aliases": list(aliases)}
    validate_json_schema(bound, _INPUT_SCHEMA)
    return bound


_HANDOFF_PAYLOAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["ocr_text", "source_kind"],
    "additionalProperties": False,
    "properties": {
        "ocr_text": {"type": "string", "minLength": 1, "maxLength": 24000},
        "languages": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 2, "maxLength": 16},
        },
        "source_kind": {"type": "string", "const": "mfp"},
        "page_count": {"type": "integer", "minimum": 1, "maximum": 500},
        "mime_type": {"type": "string", "minLength": 1, "maxLength": 120},
    },
}

_HANDOFF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema", "idempotency_key", "payload"],
    "additionalProperties": False,
    "properties": {
        "schema": {"type": "string", "const": "skeleton.family_document_inference_handoff.v1"},
        "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 240},
        "payload": _HANDOFF_PAYLOAD_SCHEMA,
    },
}


class InferenceQueueProtocol(Protocol):
    def submit(
        self,
        *,
        request_type: str,
        model: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        max_attempts: int = 3,
        timeout_seconds: int = 120,
    ) -> tuple[str, bool]: ...


class FamilyDocumentHandoffIngestor:
    """Claims completed private OCR packets and queues immediate local inference."""

    _STATES = ("pending", "processing", "accepted", "review", "receipts")

    def __init__(
        self,
        root: str | Path,
        queue: InferenceQueueProtocol,
        *,
        model: str,
        allowed_subject_aliases: tuple[str, str, str],
        max_attempts: int = 3,
        timeout_seconds: int = 120,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.queue = queue
        aliases = tuple(alias.strip() for alias in allowed_subject_aliases)
        if len(aliases) != 3 or any(not alias for alias in aliases) or len(set(aliases)) != 3:
            raise InferenceValidationError("exact_three_subject_aliases_required")
        self.model = model
        self.allowed_subject_aliases = aliases
        self.max_attempts = max_attempts
        self.timeout_seconds = timeout_seconds
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        for state in self._STATES:
            directory = self.root / state
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)

    def ingest_one(self) -> bool:
        self._recover_processing_claims()
        for source in sorted((self.root / "pending").glob("*.json")):
            target = self.root / "processing" / source.name
            try:
                os.replace(source, target)
            except FileNotFoundError:
                continue
            try:
                value = json.loads(target.read_text(encoding="utf-8"))
                if not isinstance(value, Mapping):
                    raise InferenceValidationError("handoff_packet_not_object")
                validate_json_schema(value, _HANDOFF_SCHEMA)
                raw_payload = value["payload"]
                assert isinstance(raw_payload, Mapping)
                payload = bind_family_subject_aliases(
                    raw_payload, self.allowed_subject_aliases
                )
                build_family_document_prompt(payload)
                request_id, created = self.queue.submit(
                    request_type=REQUEST_TYPE,
                    model=self.model,
                    payload=payload,
                    idempotency_key=str(value["idempotency_key"]),
                    max_attempts=self.max_attempts,
                    timeout_seconds=self.timeout_seconds,
                )
                self._write_receipt(
                    source.stem,
                    {
                        "schema": "skeleton.family_document_inference_handoff_receipt.v1",
                        "status": "QUEUED" if created else "DUPLICATE",
                        "request_id": request_id,
                    },
                )
                os.replace(target, self.root / "accepted" / target.name)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, InferenceValidationError) as exc:
                self._write_receipt(
                    source.stem,
                    {
                        "schema": "skeleton.family_document_inference_handoff_receipt.v1",
                        "status": "REVIEW",
                        "reason": _safe_handoff_reason(str(exc)),
                    },
                )
                os.replace(target, self.root / "review" / target.name)
            return True
        return False

    def _recover_processing_claims(self) -> None:
        for claimed in sorted((self.root / "processing").glob("*.json")):
            pending = self.root / "pending" / claimed.name
            if pending.exists():
                os.replace(claimed, self.root / "review" / claimed.name)
            else:
                os.replace(claimed, pending)

    def status(self) -> dict[str, int]:
        return {
            state: sum(1 for _ in (self.root / state).glob("*.json"))
            for state in ("pending", "processing", "accepted", "review")
        }

    def _write_receipt(self, stem: str, value: Mapping[str, Any]) -> None:
        path = self.root / "receipts" / f"{stem}.json"
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)


def _safe_handoff_reason(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "_.:-" else "_"
        for character in value
    )
    return normalized[:96] or "handoff_invalid"


FAMILY_DOCUMENT_ADAPTER = AdapterSpec(
    request_type=REQUEST_TYPE,
    prompt_builder=build_family_document_prompt,
    output_validator=validate_family_document_output,
    output_schema=_OUTPUT_SCHEMA,
)
