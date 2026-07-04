from __future__ import annotations

import math

import pytest

import core.memory_gateway_policy as gateway_policy
from core.memory_gateway_policy import MemoryGatewayPolicyError, validate_public_payload
from core.memory_value_validation import MemoryValueError, validate_memory_value


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
