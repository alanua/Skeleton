from __future__ import annotations

import math

import pytest

import core.memory_gateway_policy as gateway_policy
from core.memory_gateway_policy import (
    MemoryGatewayPolicyError,
    build_command_receipt,
    validate_public_payload,
)
from core.memory_value_validation import MemoryValueError, validate_memory_value


SYNTHETIC_PRIVATE_LOCAL_RECORD = {
    "classification": "PRIVATE_LOCAL",
    "note": "synthetic customer workspace /customers/acme-sample/secrets.env",
    "secret_ref": "bitwarden://vault/synthetic-login-item",
    "domain": "legal_private",
}


def test_memory_value_accepts_free_form_nested_json() -> None:
    payload = {
        "note": "ordinary free form text with punctuation !? [] {}",
        "nested": {"value": "alpha beta", "items": [1, True, None]},
    }
    assert validate_memory_value(payload) == payload


def test_legacy_lexical_registry_is_absent() -> None:
    assert not hasattr(gateway_policy, "_FORBIDDEN_PUBLIC_MARKERS")


def test_public_receipt_drops_unregistered_value_fields() -> None:
    payload = {
        "schema": "skeleton.example.v1",
        "status": "DONE",
        "canonical_ref": "skeleton.notes:note-1",
        "value": {"note": "free form text"},
        "unexpected": "not part of receipt contract",
    }
    assert validate_public_payload(payload) == {
        "schema": "skeleton.example.v1",
        "status": "DONE",
        "canonical_ref": "skeleton.notes:note-1",
    }


def test_status_count_summary_does_not_promote_private_local_records() -> None:
    payload = {
        "schema": "skeleton.memory_gateway.summary.v1",
        "status": "DONE",
        "aggregate_counts": {
            "record_count": 1,
            "blocked_count": 1,
            "private_local_records": [SYNTHETIC_PRIVATE_LOCAL_RECORD],
            "customer_path": "/customers/acme-sample/private-memory.json",
            "bitwarden_reference": "bitwarden://vault/synthetic-login-item",
        },
        "fallback_payload": SYNTHETIC_PRIVATE_LOCAL_RECORD,
    }

    assert validate_public_payload(payload) == {
        "schema": "skeleton.memory_gateway.summary.v1",
        "status": "DONE",
        "aggregate_counts": {
            "record_count": 1,
            "blocked_count": 1,
        },
    }


def test_typed_query_report_strips_private_fallback_records() -> None:
    receipt = build_command_receipt(
        "graph.query_code",
        {
            "schema": "skeleton.memory_gateway.graph_query.v1",
            "namespace": "skeleton",
            "project_id": "skeleton",
            "authoritative": False,
            "query_report": {
                "schema": "skeleton.memory_gateway.query_report.v1",
                "status": "DONE",
                "public_safe": True,
                "synthetic_only": True,
                "aggregate_counts": {
                    "record_count": 1,
                    "private_local_count": 1,
                    "customer_path": "/customers/acme-sample/private-memory.json",
                },
                "records": [SYNTHETIC_PRIVATE_LOCAL_RECORD],
                "fallback_payload": {
                    "status": "PRIVATE_LOCAL",
                    "secret_ref": "bitwarden://vault/synthetic-login-item",
                },
            },
        },
    )

    assert receipt == {
        "schema": "skeleton.memory_gateway.graph_query.v1",
        "namespace": "skeleton",
        "project_id": "skeleton",
        "authoritative": False,
        "query_report": {
            "schema": "skeleton.memory_gateway.query_report.v1",
            "status": "DONE",
            "public_safe": True,
            "synthetic_only": True,
            "aggregate_counts": {"record_count": 1},
        },
    }


def test_cross_domain_fallback_records_are_not_public_metadata() -> None:
    receipt = build_command_receipt(
        "graph.get_case_timeline",
        {
            "schema": "skeleton.memory_gateway.case_timeline.v1",
            "case_ref": "synthetic-case-001",
            "timeline": [
                {
                    "event_ref": "event-001",
                    "domain": "legal_private",
                    "event_type": "synthetic_status_snapshot",
                    "source_ref": "private_memory:synthetic",
                    "record": SYNTHETIC_PRIVATE_LOCAL_RECORD,
                    "source_path": "/customers/acme-sample/legal/case-notes.md",
                    "bitwarden_reference": "bitwarden://vault/synthetic-case-token",
                }
            ],
            "aggregate_counts": {
                "record_count": 1,
                "cross_domain_record": SYNTHETIC_PRIVATE_LOCAL_RECORD,
            },
            "private_payloads_included": False,
        },
    )

    assert receipt == {
        "schema": "skeleton.memory_gateway.case_timeline.v1",
        "case_ref": "synthetic-case-001",
        "timeline": [
            {
                "event_ref": "event-001",
                "domain": "legal_private",
                "event_type": "synthetic_status_snapshot",
                "source_ref": "private_memory:synthetic",
            }
        ],
        "aggregate_counts": {"record_count": 1},
        "private_payloads_included": False,
    }


def test_lookup_identifier_remains_structurally_validated() -> None:
    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        validate_public_payload({"lookup_key": "not a valid identifier"})
    assert excinfo.value.reason_code == "UNSAFE_PUBLIC_PAYLOAD"


def test_memory_value_validation_returns_deep_copy() -> None:
    source = {"items": [{"value": "original"}]}
    validated = validate_memory_value(source)
    validated["items"][0]["value"] = "changed"
    assert source["items"][0]["value"] == "original"


def test_memory_value_rejects_non_json_and_non_finite_values() -> None:
    with pytest.raises(MemoryValueError):
        validate_memory_value({"value": object()})
    with pytest.raises(MemoryValueError):
        validate_memory_value({"value": math.nan})
