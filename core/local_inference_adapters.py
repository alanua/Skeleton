from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


class InferenceValidationError(ValueError):
    """Raised when an inference request or model result violates its contract."""


PromptBuilder = Callable[[Mapping[str, Any]], str]
OutputValidator = Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class AdapterSpec:
    request_type: str
    prompt_builder: PromptBuilder
    output_validator: OutputValidator
    output_schema: Mapping[str, Any] | None = None


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, AdapterSpec] = {}

    def register(self, adapter: AdapterSpec) -> None:
        request_type = adapter.request_type.strip()
        if not request_type or request_type in self._adapters:
            raise InferenceValidationError("adapter_registration_invalid")
        self._adapters[request_type] = adapter

    def get(self, request_type: str) -> AdapterSpec:
        try:
            return self._adapters[request_type]
        except KeyError as exc:
            raise InferenceValidationError("adapter_not_registered") from exc

    def request_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))


def build_default_registry() -> AdapterRegistry:
    from core.family_document_local_inference import FAMILY_DOCUMENT_ADAPTER

    registry = AdapterRegistry()
    registry.register(FAMILY_DOCUMENT_ADAPTER)
    return registry


def validate_json_schema(value: Any, schema: Mapping[str, Any], *, path: str = "$") -> None:
    """Validate the strict JSON-schema subset used by local inference contracts.

    Supported keywords are deliberately bounded: type, required, properties,
    additionalProperties, items, enum, const, min/max length, min/max items,
    minimum and maximum. Unknown schema keywords fail closed.
    """

    supported = {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "pattern",
    }
    unknown = set(schema) - supported
    if unknown:
        raise InferenceValidationError(f"schema_keyword_unsupported:{path}")

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        raise InferenceValidationError(f"schema_type_invalid:{path}")

    if "const" in schema and value != schema["const"]:
        raise InferenceValidationError(f"schema_const_invalid:{path}")
    if "enum" in schema and value not in schema["enum"]:
        raise InferenceValidationError(f"schema_enum_invalid:{path}")

    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            raise InferenceValidationError(f"schema_string_too_short:{path}")
        if isinstance(maximum, int) and len(value) > maximum:
            raise InferenceValidationError(f"schema_string_too_long:{path}")
        pattern = schema.get("pattern")
        if pattern is not None:
            import re

            if re.fullmatch(str(pattern), value) is None:
                raise InferenceValidationError(f"schema_pattern_invalid:{path}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            raise InferenceValidationError(f"schema_number_too_small:{path}")
        if isinstance(maximum, (int, float)) and value > maximum:
            raise InferenceValidationError(f"schema_number_too_large:{path}")

    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise InferenceValidationError(f"schema_array_too_short:{path}")
        if isinstance(maximum, int) and len(value) > maximum:
            raise InferenceValidationError(f"schema_array_too_long:{path}")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                validate_json_schema(item, item_schema, path=f"{path}[{index}]")

    if isinstance(value, Mapping):
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise InferenceValidationError(f"schema_required_invalid:{path}")
        for key in required:
            if key not in value:
                raise InferenceValidationError(f"schema_required_missing:{path}.{key}")
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise InferenceValidationError(f"schema_properties_invalid:{path}")
        additional = schema.get("additionalProperties", True)
        if additional is False:
            unexpected = set(value) - set(properties)
            if unexpected:
                raise InferenceValidationError(f"schema_property_unexpected:{path}")
        for key, child in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, Mapping):
                validate_json_schema(child, child_schema, path=f"{path}.{key}")
            elif isinstance(additional, Mapping):
                validate_json_schema(child, additional, path=f"{path}.{key}")


def _matches_type(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches_type(value, item) for item in expected)
    return {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(str(expected), False)
