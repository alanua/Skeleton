from __future__ import annotations

import pytest

from core.local_inference_adapters import (
    AdapterRegistry,
    AdapterSpec,
    InferenceValidationError,
    validate_json_schema,
)


def test_registry_rejects_duplicate_and_unknown() -> None:
    adapter = AdapterSpec("demo.task", lambda payload: str(payload), lambda value: value)
    registry = AdapterRegistry()
    registry.register(adapter)
    assert registry.get("demo.task") is adapter
    with pytest.raises(InferenceValidationError):
        registry.register(adapter)
    with pytest.raises(InferenceValidationError):
        registry.get("missing.task")


def test_strict_schema_rejects_extra_property() -> None:
    schema = {
        "type": "object",
        "required": ["value"],
        "additionalProperties": False,
        "properties": {"value": {"type": "integer", "minimum": 1}},
    }
    validate_json_schema({"value": 1}, schema)
    with pytest.raises(InferenceValidationError):
        validate_json_schema({"value": 1, "extra": True}, schema)
    with pytest.raises(InferenceValidationError):
        validate_json_schema({"value": 0}, schema)


def test_unknown_schema_keyword_fails_closed() -> None:
    with pytest.raises(InferenceValidationError):
        validate_json_schema({}, {"type": "object", "oneOf": []})
