from __future__ import annotations

import pytest

from core.runner_vnext_cutover_bridge import (
    VNextCutoverBridgeError,
    evaluate_vnext_cutover_bridge,
)


def metadata(**overrides):
    value = {
        "issue_number": 3951,
        "legacy_route": "code_generation",
        "repo": "alanua/Skeleton",
        "branch": "runner/issue-3951",
        "base_sha": "a" * 40,
        "allowed_files": ("core/example.py",),
        "privacy_boundary": "PUBLIC_SAFE_REPOSITORY_ONLY",
        "requested_capabilities": ("repository_write_allowlisted", "test_execution"),
    }
    value.update(overrides)
    return value


def test_off_mode_is_behaviorally_inert_without_parsing_metadata() -> None:
    decision = evaluate_vnext_cutover_bridge(configured_mode=None, normalized_metadata={})
    assert decision.status == "off"
    assert decision.allow_legacy_execution is True
    assert decision.side_effects_executed is False


def test_shadow_observes_green_code_edit_without_affecting_execution() -> None:
    decision = evaluate_vnext_cutover_bridge(configured_mode="shadow", normalized_metadata=metadata())
    assert decision.status == "shadow_observed"
    assert decision.divergence == "MATCH"
    assert decision.vnext_effect_class == "GREEN"
    assert decision.canary_eligible is True
    assert decision.allow_legacy_execution is True
    assert decision.source_task_ref == "issue:3951"
    assert decision.target_state_ref == f"git:{'a' * 40}"
    assert decision.side_effects_executed is False


def test_green_canary_allows_only_exact_green_match() -> None:
    decision = evaluate_vnext_cutover_bridge(configured_mode="green_canary", normalized_metadata=metadata())
    assert decision.status == "green_canary_pass"
    assert decision.reason_code == "VNEXT_GREEN_CANARY_MATCH"
    assert decision.allow_legacy_execution is True


def test_protected_code_edit_stays_on_legacy_protected_path() -> None:
    decision = evaluate_vnext_cutover_bridge(
        configured_mode="green_canary",
        normalized_metadata=metadata(allowed_files=("scripts/runner_poll_github_tasks.py",)),
    )
    assert decision.canary_eligible is False
    assert decision.status == "legacy_protected_path"
    assert decision.allow_legacy_execution is True
    assert decision.vnext_effect_class == "YELLOW"


def test_non_code_route_is_never_green_canary_executed() -> None:
    decision = evaluate_vnext_cutover_bridge(
        configured_mode="green_canary",
        normalized_metadata=metadata(legacy_route="publish_only"),
    )
    assert decision.canary_eligible is False
    assert decision.status == "legacy_protected_path"
    assert decision.allow_legacy_execution is True


def test_invalid_mode_and_metadata_fail_closed_to_caller() -> None:
    with pytest.raises(VNextCutoverBridgeError, match="VNEXT_CUTOVER_MODE_INVALID"):
        evaluate_vnext_cutover_bridge(configured_mode="full_send", normalized_metadata=metadata())
    with pytest.raises(VNextCutoverBridgeError, match="VNEXT_METADATA_PRIVACY_REQUIRED"):
        evaluate_vnext_cutover_bridge(configured_mode="shadow", normalized_metadata=metadata(privacy_boundary=None))


def test_public_mapping_contains_no_environment_or_command_surface() -> None:
    decision = evaluate_vnext_cutover_bridge(configured_mode="shadow", normalized_metadata=metadata())
    public = decision.to_public_mapping()
    assert "environment" not in public
    assert "shell" not in public
    assert "argv" not in public
    assert "command" not in public
