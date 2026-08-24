from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

import core.family_document_runtime as runtime_module
from core.family_document_runtime import FamilyDocumentRuntime


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "family_document_receipt.schema.json"


class _State:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def enqueue(self, payload: dict[str, object]) -> None:
        self.payloads.append(payload)


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_runtime_package_parts_match_declared_receipt_schema(monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "render_package_report", lambda _records: ["part one", "part two"])
    state = _State()
    runtime = FamilyDocumentRuntime(source=object(), archive_sink=object(), outbox=state)  # type: ignore[arg-type]

    runtime._enqueue_package_report([{"record_id": "record-1"}])

    validator = _validator()
    assert len(state.payloads) == 2
    for index, payload in enumerate(state.payloads, start=1):
        validator.validate(payload)
        assert payload["receipt_type"] == "package_part"
        assert payload["part_index"] == index
        assert payload["part_count"] == 2
        assert "record_id" not in payload


def test_intake_and_terminal_receipts_still_require_record_id() -> None:
    validator = _validator()
    for receipt_type in ("intake", "terminal"):
        validator.validate(
            {
                "schema": "skeleton.family_document_receipt.v1",
                "receipt_key": f"{receipt_type}:record-1",
                "receipt_type": receipt_type,
                "record_id": "record-1",
                "status": "DONE",
            }
        )
        errors = list(
            validator.iter_errors(
                {
                    "schema": "skeleton.family_document_receipt.v1",
                    "receipt_key": f"{receipt_type}:record-1",
                    "receipt_type": receipt_type,
                }
            )
        )
        assert errors


def test_package_part_rejects_stale_or_malformed_shapes() -> None:
    validator = _validator()
    valid = {
        "schema": "skeleton.family_document_receipt.v1",
        "receipt_key": "package:abc:0001",
        "receipt_type": "package_part",
        "package_key": "abc",
        "part_index": 1,
        "part_count": 1,
        "status": "DONE",
        "message": "ok",
    }
    validator.validate(valid)

    invalid_payloads = [
        {**valid, "receipt_type": "package_report"},
        {**valid, "record_id": "record-1"},
        {**valid, "part_index": 0},
        {**valid, "part_count": 0},
        {**valid, "message": ""},
        {**valid, "message": "x" * 4097},
        {key: value for key, value in valid.items() if key != "package_key"},
    ]
    for payload in invalid_payloads:
        assert list(validator.iter_errors(payload))
