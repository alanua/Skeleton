from __future__ import annotations

from dataclasses import dataclass

from core.hermes_worker import run_hermes_worker_dry_run


def task_packet(**overrides: object) -> dict[str, object]:
    packet: dict[str, object] = {
        "schema": "hermes.task_packet.v0",
        "task_id": "ISSUE-949",
        "title": "Dry run Hermes Worker v0 executor",
        "goal": "Return a public-safe dry-run result without executing work.",
        "worker_mode": "dry_run",
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
        "scope": ["public-safe dry-run validation"],
        "allowed_files": [
            "core/hermes_worker.py",
            "tests/test_hermes_worker.py",
            "docs/hermes_worker_v0.md",
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
                "command": "python3 -m pytest tests/test_hermes_worker.py",
                "purpose": "Validate the dry-run executor.",
                "mutating": False,
            }
        ],
        "expected_outputs": ["review_summary"],
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
        "skill_id": "dry_run_reviewer",
        "version": "v0",
        "name": "Dry Run Reviewer",
        "summary": "Reviews public-safe Hermes Worker dry-run packets.",
        "activation_state": "review_only",
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
                "name": "dry_run_result",
                "description": "Public-safe structured dry-run result.",
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


@dataclass
class PacketObject:
    schema: str
    task_id: str
    title: str
    goal: str
    worker_mode: str
    public_safe: bool
    no_secrets: bool
    no_runtime_mutation: bool
    approval_required: bool
    source_context: list[dict[str, object]]
    scope: list[str]
    allowed_files: list[str]
    forbidden_actions: list[str]
    validation: list[dict[str, object]]
    expected_outputs: list[str]
    authority_boundary: dict[str, object]
    skill_manifest_ref: str | None = None


def test_valid_dry_run_returns_structured_public_safe_result() -> None:
    result = run_hermes_worker_dry_run(task_packet(), skill_manifest())

    assert result == {
        "status": "DRY_RUN_OK",
        "task_id": "ISSUE-949",
        "skill_id": "dry_run_reviewer",
        "mode": "dry_run",
        "decision": {
            "allowed": True,
            "reason": "packet_satisfies_public_safe_dry_run_contract",
        },
        "warnings": [],
        "diagnostics": {
            "schema": "hermes.worker_dry_run_result.v0",
            "safe_statuses": [
                "BLOCKED",
                "DRY_RUN_OK",
                "OPERATOR_APPROVAL_REQUIRED",
                "REVIEW_REQUIRED",
            ],
            "missing_fields": [],
            "invalid_fields": [],
            "redacted_fields": [],
        },
    }


def test_missing_fields_require_review_without_echoing_payload() -> None:
    packet = task_packet()
    del packet["goal"]
    packet["private_payload"] = "do-not-return"

    result = run_hermes_worker_dry_run(packet, skill_manifest())

    assert result["status"] == "REVIEW_REQUIRED"
    assert result["decision"] == {
        "allowed": False,
        "reason": "packet_requires_review_before_dry_run",
    }
    assert result["diagnostics"]["missing_fields"] == ["goal"]
    assert "do-not-return" not in repr(result)


def test_forbidden_live_mode_is_blocked() -> None:
    result = run_hermes_worker_dry_run(
        task_packet(worker_mode="live", no_runtime_mutation=False),
        skill_manifest(),
    )

    assert result["status"] == "BLOCKED"
    assert result["mode"] == "live"
    assert result["decision"] == {
        "allowed": False,
        "reason": "packet_requests_unsafe_or_live_execution",
    }


def test_private_payload_fields_are_redacted_from_result() -> None:
    result = run_hermes_worker_dry_run(
        task_packet(private_payload={"token": "abc123", "note": "hidden"}),
        skill_manifest(secret_note="should not leak"),
    )

    assert result["status"] == "DRY_RUN_OK"
    assert result["warnings"] == ["private_or_sensitive_fields_redacted"]
    assert result["diagnostics"]["redacted_fields"] == [
        "private_payload",
        "secret_note",
    ]
    assert "abc123" not in repr(result)
    assert "should not leak" not in repr(result)


def test_operator_approval_required_skill_tier() -> None:
    result = run_hermes_worker_dry_run(
        task_packet(),
        skill_manifest(skill_tier="operator_approval_required"),
    )

    assert result["status"] == "OPERATOR_APPROVAL_REQUIRED"
    assert result["decision"] == {
        "allowed": False,
        "reason": "skill_tier_requires_operator_approval",
    }


def test_task_packet_can_be_plain_object() -> None:
    packet = PacketObject(**task_packet())

    result = run_hermes_worker_dry_run(packet, skill_manifest())

    assert result["status"] == "DRY_RUN_OK"
    assert result["task_id"] == "ISSUE-949"
