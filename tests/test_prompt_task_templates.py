from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

import pytest
from jsonschema import Draft202012Validator


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "prompt_task_template.schema.json"
FIXTURE_DIR = ROOT / "fixtures" / "prompt_tasks"
FIXTURE_PATHS = sorted(FIXTURE_DIR.glob("*.json"))

REQUIRED_NONEMPTY_LIST_PATHS = [
    ("context", "assumptions"),
    ("context", "constraints"),
    ("reference_artifacts",),
    ("allowed_files",),
    ("forbidden",),
    ("acceptance_criteria",),
    ("required_validation",),
    ("expected_output", "required_sections"),
    ("expected_output", "artifact_paths"),
    ("evidence_receipt", "changed_files"),
    ("evidence_receipt", "validation_results"),
]


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    loaded = load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(loaded)
    return loaded


@pytest.fixture(scope="module")
def validator(schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(schema)


@pytest.fixture(scope="module")
def fixtures() -> list[dict[str, Any]]:
    assert [path.name for path in FIXTURE_PATHS] == [
        "bounded_implementation.json",
        "bug_investigation.json",
        "review_verification.json",
    ]
    return [load_json(path) for path in FIXTURE_PATHS]


def assert_invalid(validator: Draft202012Validator, instance: dict[str, Any]) -> None:
    errors = list(validator.iter_errors(instance))
    assert errors, "instance unexpectedly validated"


def set_path(instance: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    target: Any = instance
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def collect_property_names(node: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            names.update(properties)
        for value in node.values():
            names.update(collect_property_names(value))
    elif isinstance(node, list):
        for value in node:
            names.update(collect_property_names(value))
    return names


def test_schema_and_all_fixtures_validate(
    validator: Draft202012Validator,
    fixtures: list[dict[str, Any]],
) -> None:
    for fixture in fixtures:
        validator.validate(fixture)


def test_every_required_top_level_field_is_enforced(
    schema: dict[str, Any],
    validator: Draft202012Validator,
    fixtures: list[dict[str, Any]],
) -> None:
    sample = fixtures[0]
    for field in schema["required"]:
        candidate = copy.deepcopy(sample)
        candidate.pop(field)
        assert_invalid(validator, candidate)


@pytest.mark.parametrize(
    ("path", "unknown_key"),
    [
        ((), "unexpected_top_level"),
        (("context",), "unexpected_context"),
        (("reference_artifacts", 0), "embedded_content"),
        (("acceptance_criteria", 0), "unexpected_criterion"),
        (("acceptance_criteria", 0, "expected_result"), "unexpected_result"),
        (("required_validation", 0), "unexpected_validation"),
        (("expected_output",), "unexpected_output"),
        (("approval_requirement",), "unexpected_approval"),
        (("rollback_requirement",), "unexpected_rollback"),
        (("evidence_receipt",), "unexpected_receipt"),
        (("evidence_receipt", "validation_results", 0), "unexpected_validation_receipt"),
    ],
)
def test_unknown_top_level_and_nested_keys_fail(
    validator: Draft202012Validator,
    fixtures: list[dict[str, Any]],
    path: tuple[Any, ...],
    unknown_key: str,
) -> None:
    candidate = copy.deepcopy(fixtures[0])
    target: Any = candidate
    for key in path:
        target = target[key]
    target[unknown_key] = "not allowed"
    assert_invalid(validator, candidate)


@pytest.mark.parametrize("risk", ["yellow", "red"])
def test_yellow_and_red_risk_reject_none_approval(
    validator: Draft202012Validator,
    fixtures: list[dict[str, Any]],
    risk: str,
) -> None:
    candidate = copy.deepcopy(fixtures[0])
    candidate["risk"] = risk
    candidate["approval_requirement"]["mode"] = "none"
    if risk == "red":
        candidate["rollback_requirement"]["mode"] = "revert_commit"
    assert_invalid(validator, candidate)


def test_red_risk_rejects_not_applicable_rollback(
    validator: Draft202012Validator,
    fixtures: list[dict[str, Any]],
) -> None:
    candidate = copy.deepcopy(fixtures[0])
    candidate["risk"] = "red"
    candidate["approval_requirement"]["mode"] = "operator"
    candidate["rollback_requirement"]["mode"] = "not_applicable"
    assert_invalid(validator, candidate)


@pytest.mark.parametrize("path", REQUIRED_NONEMPTY_LIST_PATHS)
def test_required_bounded_lists_reject_empty_values(
    validator: Draft202012Validator,
    fixtures: list[dict[str, Any]],
    path: tuple[str, ...],
) -> None:
    candidate = copy.deepcopy(fixtures[0])
    set_path(candidate, path, [])
    assert_invalid(validator, candidate)


def test_all_arrays_and_strings_in_schema_are_bounded(schema: dict[str, Any]) -> None:
    unbounded: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if node.get("type") == "array" and "maxItems" not in node:
                unbounded.append(f"{path}: array missing maxItems")
            if (
                node.get("type") == "string"
                and "maxLength" not in node
                and "const" not in node
                and "enum" not in node
                and "pattern" not in node
            ):
                unbounded.append(f"{path}: string missing maxLength/enum/const/pattern")
            for key, value in node.items():
                walk(value, f"{path}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}/{index}")

    walk(schema, "$")
    assert unbounded == []


def test_schema_has_no_executable_or_secret_authority_fields(
    schema: dict[str, Any],
    validator: Draft202012Validator,
    fixtures: list[dict[str, Any]],
) -> None:
    property_names = {name.lower() for name in collect_property_names(schema)}
    forbidden_names = {
        "command",
        "commands",
        "shell",
        "tools",
        "tool_authority",
        "api_key",
        "secret",
        "secrets",
        "credential",
        "credentials",
        "embedded_content",
    }
    assert property_names.isdisjoint(forbidden_names)

    for field in forbidden_names:
        candidate = copy.deepcopy(fixtures[0])
        candidate[field] = "forbidden"
        assert_invalid(validator, candidate)

    for field in ["secret", "api_key", "credential", "content", "embedded_content"]:
        candidate = copy.deepcopy(fixtures[0])
        candidate["reference_artifacts"][0][field] = "forbidden"
        assert_invalid(validator, candidate)


def test_fixture_ids_are_unique_and_references_are_metadata_only(
    fixtures: list[dict[str, Any]],
) -> None:
    for fixture in fixtures:
        acceptance_ids = [item["id"] for item in fixture["acceptance_criteria"]]
        validation_ids = [item["id"] for item in fixture["required_validation"]]
        assert len(acceptance_ids) == len(set(acceptance_ids))
        assert len(validation_ids) == len(set(validation_ids))
        for reference in fixture["reference_artifacts"]:
            assert set(reference) <= {"kind", "identifier", "description", "checksum"}


def test_fixtures_are_provider_neutral_and_synthetic() -> None:
    text = "\n".join(path.read_text(encoding="utf-8") for path in FIXTURE_PATHS)
    lowered = text.lower()

    forbidden_tokens = [
        "claude.md",
        ".claude",
        ".codex",
        ".gemini",
        "/council",
        "prompt marketplace",
        "codex exec",
        "gemini -p",
        "ollama run",
        "cursor-agent",
        "api_key",
        "authorization:",
        "/home/",
        "c:\\",
        "private_key",
    ]
    for token in forbidden_tokens:
        assert token not in lowered

    for path in FIXTURE_PATHS:
        fixture = load_json(path)
        assert fixture["privacy_boundary"] == "public_safe_synthetic_only"
        assert fixture["evidence_receipt"]["runtime_mutation_status"] == "none"
        constraints = " ".join(fixture["context"]["constraints"]).lower()
        assert "does not override policy" in constraints
        assert "no external action or runtime mutation" in constraints


def test_json_serialization_is_deterministic(
    schema: dict[str, Any],
    fixtures: list[dict[str, Any]],
) -> None:
    for document in [schema, *fixtures]:
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
        reparsed = json.loads(canonical)
        assert json.dumps(reparsed, sort_keys=True, separators=(",", ":")) == canonical
