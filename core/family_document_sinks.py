from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from core.local_document_ocr import run_bounded_argv

GATEWAY_REQUEST_SCHEMA = "skeleton.memory_gateway.request.v1"
PRIVATE_MUTATION_SCHEMA = "skeleton.private_memory_gateway.mutation.v1"
NAMESPACE = "skeleton"
DATASET_ID = "family_documents"
FACT_NAMESPACE = "family_documents"
MUTATE_COMMAND = "skeleton.memory.private_mutate"
READ_COMMAND = "skeleton.memory.private_read_exact"
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class SinkError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class AdapterResult:
    status: str
    payload: Mapping[str, Any]


Runner = Callable[[Sequence[str], str, int, int], tuple[int, str, str]]


class JsonCommandAdapter:
    def __init__(self, command: Sequence[str], *, runner: Runner | None = None, timeout_seconds: int = 120, max_output_bytes: int = 1_000_000) -> None:
        normalized = tuple(command)
        if not normalized or not Path(normalized[0]).is_absolute() or any(not isinstance(value, str) or not value or "\x00" in value for value in normalized):
            raise SinkError("adapter_command_invalid")
        self.command = normalized
        self.runner = runner or self._run
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    def execute(self, payload: Mapping[str, Any]) -> AdapterResult:
        code, stdout, _stderr = self.runner(self.command, _strict_json(payload), self.timeout_seconds, self.max_output_bytes)
        if code != 0:
            raise SinkError("adapter_failed")
        try:
            response = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SinkError("adapter_response_invalid") from exc
        if not isinstance(response, Mapping):
            raise SinkError("adapter_response_invalid")
        response_payload = response.get("payload")
        status = response.get("status")
        if isinstance(response_payload, Mapping) and isinstance(response_payload.get("status"), str):
            status = response_payload.get("status")
        if not isinstance(status, str):
            raise SinkError("adapter_response_invalid")
        return AdapterResult(status, dict(response))

    @staticmethod
    def _run(command: Sequence[str], input_text: str, timeout: int, max_output: int) -> tuple[int, str, str]:
        try:
            result = run_bounded_argv(command, Path.cwd(), timeout, max_output, input_bytes=input_text.encode("utf-8"))
        except Exception:
            return 1, "", ""
        return result.returncode, result.stdout.decode("utf-8", errors="replace"), result.stderr.decode("utf-8", errors="replace")


class MemoryGatewaySink:
    def __init__(self, adapter: JsonCommandAdapter, *, approval_ref: str) -> None:
        self.adapter = adapter
        self.approval_ref = _safe_token(approval_ref, "approval_ref")

    def commit_and_readback(self, record: Mapping[str, Any], *, source_hash: str, expected_revision: int | None = None) -> dict[str, object]:
        mutation = build_private_mutation(record, approval_ref=self.approval_ref, source_hash=source_hash, expected_revision=expected_revision)
        mutation_result = self.adapter.execute(mutation)
        if mutation_result.status not in {"DONE", "COMMITTED", "SUCCESS", "DEGRADED", "IDEMPOTENT"}:
            raise SinkError("memory_mutation_failed")
        payload = mutation["payload"]
        read_result = self.adapter.execute(build_exact_read(payload["fact_namespace"], payload["fact_id"]))
        read_payload = read_result.payload.get("payload")
        if not isinstance(read_payload, Mapping) or read_payload.get("authoritative") is not True:
            raise SinkError("memory_exact_read_failed")
        value = read_payload.get("value")
        if not isinstance(value, Mapping):
            raise SinkError("memory_exact_read_value_missing")
        if value.get("document_id") != record.get("document_id"):
            raise SinkError("memory_exact_read_mismatch")
        return {"status": "DONE", "canonical_ref": f"{payload['fact_namespace']}:{payload['fact_id']}", "idempotency_key": payload["idempotency_key"], "authoritative": True, "mutation_status": mutation_result.status}


class CalendarSink:
    def __init__(self, adapter: JsonCommandAdapter) -> None:
        self.adapter = adapter

    def upsert(self, event: Mapping[str, Any]) -> str:
        event_type, event_id = event.get("event_type"), event.get("event_id")
        if not isinstance(event_type, str) or not isinstance(event_id, str):
            raise SinkError("calendar_event_invalid")
        calendar_event = dict(event)
        calendar_event.pop("document_id", None)
        result = self.adapter.execute({"schema": "skeleton.family_document.calendar_upsert.v1", "operation": "upsert", "idempotency_key": event_id, "event": calendar_event})
        if result.status not in {"DONE", "ACCEPTED", "IDEMPOTENT", "SUCCESS"}:
            raise SinkError("calendar_upsert_failed")
        return result.status


def build_private_mutation(record: Mapping[str, Any], *, approval_ref: str, source_hash: str, expected_revision: int | None = None, actor_ref: str = "skeleton.family_document_intake", reason_code: str = "family_document_record_commit") -> dict[str, Any]:
    document_id = record.get("document_id")
    archive_hash = record.get("archive", {}).get("sha256") if isinstance(record.get("archive"), Mapping) else None
    if not isinstance(document_id, str) or not document_id:
        raise SinkError("record_document_id_invalid")
    if not isinstance(source_hash, str) or len(source_hash) != 64:
        raise SinkError("source_hash_invalid")
    if archive_hash is not None and archive_hash != source_hash:
        raise SinkError("archive_source_hash_mismatch")
    if expected_revision is not None and (isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0):
        raise SinkError("expected_revision_invalid")
    fact_id = f"document:{_digest(document_id, str(record.get('record_revision', 'v1')))[:48]}"
    idempotency_key = "document:" + _digest(document_id, source_hash, str(record.get("record_revision", "v1")))
    value = dict(record)
    value["relations"] = {
        "duplicate_ids": list(record.get("duplicate_relations", [])),
        "version_ids": list(record.get("version_relations", [])),
        "calendar_event_ids": [event.get("event_id") for event in record.get("event_candidates", []) if isinstance(event, Mapping) and isinstance(event.get("event_id"), str)],
        "source_identity": record.get("source", {}).get("source_identity") if isinstance(record.get("source"), Mapping) else None,
    }
    envelope = {"schema": GATEWAY_REQUEST_SCHEMA, "namespace": NAMESPACE, "command": MUTATE_COMMAND, "payload": {"schema": PRIVATE_MUTATION_SCHEMA, "operation": "put", "project_id": NAMESPACE, "dataset_id": DATASET_ID, "expected_revision": expected_revision, "actor_ref": _safe_token(actor_ref, "actor_ref"), "reason_code": _safe_token(reason_code, "reason_code"), "approval_ref": _safe_token(approval_ref, "approval_ref"), "fact_namespace": FACT_NAMESPACE, "fact_id": fact_id, "value": value, "source_hash": source_hash, "idempotency_key": idempotency_key}}
    _strict_json(envelope)
    return envelope


def build_exact_read(fact_namespace: str, fact_id: str) -> dict[str, Any]:
    namespace, identifier = _safe_token(fact_namespace, "fact_namespace"), _safe_token(fact_id, "fact_id")
    request = {"schema": GATEWAY_REQUEST_SCHEMA, "namespace": NAMESPACE, "command": READ_COMMAND, "payload": {"project_id": NAMESPACE, "dataset_id": DATASET_ID, "canonical_ref": f"{namespace}:{identifier}"}}
    _strict_json(request)
    return request


def _safe_token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _SAFE_TOKEN_RE.fullmatch(value) is None or any(marker in value.casefold() for marker in ("secret", "password", "credential", "/", "\\")):
        raise SinkError(f"{field_name}_invalid")
    return value


def _strict_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise SinkError("strict_json_required") from exc


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
