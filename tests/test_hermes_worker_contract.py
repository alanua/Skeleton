from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "hermes_worker_v0.md"
TASK_PACKET_SCHEMA_PATH = ROOT / "schemas" / "hermes_task_packet.schema.json"
SKILL_MANIFEST_SCHEMA_PATH = ROOT / "schemas" / "hermes_skill_manifest.schema.json"
PLAN_PATH = ROOT / "projects" / "skeleton" / "HERMES_WORKER_V0_PLAN.md"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def task_packet(**overrides: object) -> dict[str, object]:
    packet: dict[str, object] = {
        "schema": "hermes.task_packet.v0",
        "task_id": "ISSUE-935",
        "title": "Add Hermes Worker v0 contract",
        "goal": "Define public-safe contract artifacts and tests.",
        "worker_mode": "contract_test",
        "public_safe": True,
        "no_secrets": True,
        "no_runtime_mutation": True,
        "approval_required": True,
        "source_context": [
            {
                "source_type": "issue",
                "reference": "public task assignment",
                "public_safe": True,
                "read_only": True,
            }
        ],
        "scope": ["public-safe documentation and schemas"],
        "allowed_files": [
            "docs/hermes_worker_v0.md",
            "schemas/hermes_task_packet.schema.json",
            "schemas/hermes_skill_manifest.schema.json",
            "tests/test_hermes_worker_contract.py",
            "projects/skeleton/HERMES_WORKER_V0_PLAN.md",
        ],
        "forbidden_actions": [
            "server_install",
            "runtime_service_change",
            "workflow_change",
            "protected_file_change",
            "private_data_access",
            "secret_access",
            "queue_mutation",
            "issue_mutation",
            "merge",
            "deploy",
            "publish",
            "host_maintenance",
            "canon_promotion",
        ],
        "validation": [
            {
                "command": "python -m pytest tests/test_hermes_worker_contract.py",
                "purpose": "Validate static public-safe contract artifacts.",
                "mutating": False,
            }
        ],
        "expected_outputs": [
            "public_safe_contract",
            "task_packet_schema",
            "skill_manifest_schema",
            "contract_tests",
            "draft_pr",
        ],
        "authority_boundary": {
            "review_only": True,
            "mutation_allowed": False,
            "runtime_install_allowed": False,
            "approval_path": "authorized operator or reviewed process",
        },
        "skill_manifest_ref": "schemas/hermes_skill_manifest.schema.json",
    }
    packet.update(overrides)
    return packet


def skill_manifest(**overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema": "hermes.skill_manifest.v0",
        "skill_id": "contract_reviewer",
        "version": "v0",
        "name": "Contract Reviewer",
        "summary": "Reviews public-safe Hermes Worker contract packets.",
        "activation_state": "proposed",
        "public_safe": True,
        "approval_required": True,
        "runtime_install_allowed": False,
        "network_required": False,
        "inputs": [
            {
                "name": "task_packet",
                "description": "Public-safe Hermes task packet.",
                "public_safe": True,
            }
        ],
        "outputs": [
            {
                "name": "review_summary",
                "description": "Public-safe contract review summary.",
                "public_safe": True,
            }
        ],
        "allowed_operations": [
            "read_public_safe_context",
            "normalize_task_packet",
            "validate_contract_shape",
            "prepare_review_summary",
        ],
        "forbidden_operations": [
            "execute_shell",
            "patch_files",
            "install_server",
            "start_runtime_service",
            "change_workflows",
            "access_private_data",
            "access_secrets",
            "mutate_queue",
            "mutate_issues",
            "merge",
            "deploy",
            "publish",
            "promote_canon",
            "approve_skill",
            "activate_skill",
        ],
        "authority_boundary": {
            "review_only": True,
            "mutation_allowed": False,
            "activation_allowed": False,
            "approval_path": "authorized operator or reviewed process",
        },
    }
    manifest.update(overrides)
    return manifest


def assert_required_fields_present(schema: dict, payload: dict) -> None:
    assert set(schema["required"]).issubset(payload)


def assert_const_fields_hold(schema: dict, payload: dict) -> None:
    for name, rules in schema["properties"].items():
        if "const" in rules and name in payload:
            assert payload[name] == rules["const"]


def test_contract_artifacts_exist() -> None:
    assert DOC_PATH.is_file()
    assert TASK_PACKET_SCHEMA_PATH.is_file()
    assert SKILL_MANIFEST_SCHEMA_PATH.is_file()
    assert PLAN_PATH.is_file()


def test_worker_contract_declares_static_public_safe_boundary() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")

    assert "static documentation and schema contract only" in text
    assert "does not add a runtime service" in text
    assert "server install" in text
    assert "workflow" in text
    assert "private data" in text
    assert "must not:" in text
    assert "execute tasks" in text
    assert "approve or activate skills" in text


def test_task_packet_schema_documents_public_safe_required_fields() -> None:
    schema = load_json(TASK_PACKET_SCHEMA_PATH)

    assert schema["$id"] == "skeleton.hermes_task_packet.schema.json"
    assert schema["properties"]["schema"]["const"] == "hermes.task_packet.v0"
    assert schema["additionalProperties"] is False

    expected_required = {
        "schema",
        "task_id",
        "title",
        "goal",
        "worker_mode",
        "public_safe",
        "no_secrets",
        "no_runtime_mutation",
        "approval_required",
        "source_context",
        "scope",
        "allowed_files",
        "forbidden_actions",
        "validation",
        "expected_outputs",
        "authority_boundary",
    }
    assert expected_required.issubset(schema["required"])
    assert schema["properties"]["public_safe"]["const"] is True
    assert schema["properties"]["no_secrets"]["const"] is True
    assert schema["properties"]["no_runtime_mutation"]["const"] is True
    assert schema["properties"]["approval_required"]["const"] is True


def test_task_packet_schema_forbids_runtime_and_private_scope() -> None:
    schema = load_json(TASK_PACKET_SCHEMA_PATH)
    forbidden = set(schema["properties"]["forbidden_actions"]["items"]["enum"])

    assert {
        "server_install",
        "runtime_service_change",
        "workflow_change",
        "protected_file_change",
        "private_data_access",
        "secret_access",
        "queue_mutation",
        "issue_mutation",
        "merge",
        "deploy",
        "publish",
        "host_maintenance",
        "canon_promotion",
    }.issubset(forbidden)


def test_representative_task_packet_satisfies_schema_invariants() -> None:
    schema = load_json(TASK_PACKET_SCHEMA_PATH)
    packet = task_packet()

    assert_required_fields_present(schema, packet)
    assert_const_fields_hold(schema, packet)
    assert packet["worker_mode"] in schema["properties"]["worker_mode"]["enum"]
    assert set(packet["expected_outputs"]).issubset(
        schema["properties"]["expected_outputs"]["items"]["enum"]
    )
    assert all(command["mutating"] is False for command in packet["validation"])
    assert packet["authority_boundary"]["mutation_allowed"] is False
    assert packet["authority_boundary"]["runtime_install_allowed"] is False


def test_task_packet_runtime_mutation_cannot_satisfy_schema_constants() -> None:
    schema = load_json(TASK_PACKET_SCHEMA_PATH)
    packet = task_packet(no_runtime_mutation=False)

    assert (
        packet["no_runtime_mutation"]
        != schema["properties"]["no_runtime_mutation"]["const"]
    )


def test_skill_manifest_schema_documents_inactive_public_safe_manifest() -> None:
    schema = load_json(SKILL_MANIFEST_SCHEMA_PATH)

    assert schema["$id"] == "skeleton.hermes_skill_manifest.schema.json"
    assert schema["properties"]["schema"]["const"] == "hermes.skill_manifest.v0"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["activation_state"]["enum"] == [
        "proposed",
        "review_only",
        "disabled",
    ]
    assert schema["properties"]["public_safe"]["const"] is True
    assert schema["properties"]["approval_required"]["const"] is True
    assert schema["properties"]["runtime_install_allowed"]["const"] is False
    assert schema["properties"]["network_required"]["const"] is False


def test_skill_manifest_schema_forbids_activation_and_execution() -> None:
    schema = load_json(SKILL_MANIFEST_SCHEMA_PATH)
    forbidden = set(schema["properties"]["forbidden_operations"]["items"]["enum"])

    assert {
        "execute_shell",
        "patch_files",
        "install_server",
        "start_runtime_service",
        "change_workflows",
        "access_private_data",
        "access_secrets",
        "mutate_queue",
        "mutate_issues",
        "merge",
        "deploy",
        "publish",
        "promote_canon",
        "approve_skill",
        "activate_skill",
    }.issubset(forbidden)


def test_representative_skill_manifest_satisfies_schema_invariants() -> None:
    schema = load_json(SKILL_MANIFEST_SCHEMA_PATH)
    manifest = skill_manifest()

    assert_required_fields_present(schema, manifest)
    assert_const_fields_hold(schema, manifest)
    assert manifest["activation_state"] in schema["properties"]["activation_state"]["enum"]
    assert set(manifest["allowed_operations"]).issubset(
        schema["properties"]["allowed_operations"]["items"]["enum"]
    )
    assert set(manifest["forbidden_operations"]).issubset(
        schema["properties"]["forbidden_operations"]["items"]["enum"]
    )
    assert manifest["authority_boundary"]["mutation_allowed"] is False
    assert manifest["authority_boundary"]["activation_allowed"] is False


def test_skill_manifest_activation_cannot_satisfy_schema_constants() -> None:
    schema = load_json(SKILL_MANIFEST_SCHEMA_PATH)
    manifest = skill_manifest(runtime_install_allowed=True)

    assert (
        manifest["runtime_install_allowed"]
        != schema["properties"]["runtime_install_allowed"]["const"]
    )


def test_plan_is_limited_to_public_contract_phase() -> None:
    text = PLAN_PATH.read_text(encoding="utf-8")

    assert "public-safe planning note" in text
    assert "Public contract and schemas" in text
    assert "cannot approve or activate" in text
