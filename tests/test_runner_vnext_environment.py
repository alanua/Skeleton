from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import jsonschema
import pytest

from core.runner_vnext_environment import (
    EnvironmentContractError,
    LaneEnvironmentPolicy,
    LaneEnvironmentPolicyRegistry,
    lane_environment_policy_hash,
    validate_lane_environment_policy,
)
from scripts import runner_poll_github_tasks as runner

ROOT = Path(__file__).resolve().parents[1]
HEAD_SHA = "a" * 40


def _write_readonly_preflight_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    workdir = tmp_path / "checkout"
    workdir.mkdir()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    ledger = state / "ledger.sqlite"
    lease = state / "lease.sqlite"
    ledger_connection = sqlite3.connect(ledger)
    ledger_connection.executescript(
        """
        CREATE TABLE runner_vnext_operation_events (
            idempotency_key TEXT NOT NULL,
            terminal INTEGER NOT NULL
        );
        CREATE TABLE runner_vnext_operation_starts (
            idempotency_key TEXT NOT NULL
        );
        """
    )
    ledger_connection.commit()
    ledger_connection.close()
    lease_connection = sqlite3.connect(lease)
    lease_connection.executescript(
        """
        CREATE TABLE runner_vnext_lane_fences (
            lane TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            fence_token INTEGER NOT NULL
        );
        CREATE TABLE runner_vnext_lane_leases (
            lane TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            task_id TEXT NOT NULL,
            owner TEXT NOT NULL,
            fence_token INTEGER NOT NULL,
            acquired_at REAL NOT NULL,
            heartbeat_at REAL NOT NULL,
            ttl_seconds REAL NOT NULL,
            target_state_ref TEXT
        );
        """
    )
    lease_connection.commit()
    lease_connection.close()
    ledger.chmod(0o600)
    lease.chmod(0o600)
    _write_attestation(state, observed=time.time() - 1.0, expires=time.time() + 30.0)
    return workdir, ledger, lease


def _write_attestation(state: Path, *, observed: float, expires: float) -> Path:
    snapshot = {
        "schema": runner.RUNNER_VNEXT_NODE_SNAPSHOT_SCHEMA,
        "node_id": "node:runner-vnext-harmless-diagnostic-runtime",
        "generation": 1,
        "route_rank": 5,
        "capabilities": ["repository_read"],
        "supported_adapters": ["adapter:harmless-diagnostic"],
        "supported_lanes": ["validate"],
        "privacy_classes": ["PUBLIC_SAFE_REPOSITORY_ONLY"],
        "resource_patterns": ["repo:docs/RUNNER_MAINTENANCE_TASKS.md"],
        "observed_at": observed,
        "expires_at": expires,
        "attestation_ref": "attestation:external-boundary:synthetic",
    }
    envelope_base = {
        "schema": "skeleton.runner_vnext_external_attestation_envelope.v1",
        "repository": "alanua/Skeleton",
        "target_issue": 4691,
        "profile": runner.RUNNER_VNEXT_EXTERNAL_ATTESTATION_PROFILE,
        "route": "diagnostic",
        "binding_sha256": "a" * 64,
        "idempotency_sha256": "b" * 64,
        "generation": 1,
        "snapshot": snapshot,
    }
    envelope = {
        **envelope_base,
        "payload_sha256": hashlib.sha256(
            json.dumps(envelope_base, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    path = state / runner.RUNNER_VNEXT_EXTERNAL_ATTESTATION_FILENAME
    path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n")
    path.chmod(0o600)
    return path


def _healthy_readonly_probe(command: list[str], **_kwargs: object) -> tuple[int, str]:
    if command[:4] == ["git", "rev-parse", "--verify", "HEAD"]:
        return 0, HEAD_SHA + "\n"
    if command[:2] == ["gh", "api"]:
        return 0, HEAD_SHA + "\n"
    if command[:2] == ["systemctl", "is-active"]:
        return 0, "active\n"
    if command[:2] == ["systemctl", "is-enabled"]:
        return 0, "enabled\n"
    if command[:2] == ["systemctl", "show"]:
        return 0, "loaded\n"
    raise AssertionError(command)


def validation_policy() -> LaneEnvironmentPolicy:
    return LaneEnvironmentPolicy(
        lane_id="lane:validation",
        inherit_keys=("HOME", "PATH", "LANG", "SKELETON_LLM_MODEL"),
        inject_keys=(),
        denied_keys=("OPENROUTER_API_KEY", "BWS_ACCESS_TOKEN", "SKELETON_TG_BOT"),
        secret_keys=(),
    )


def test_positive_allowlist_strips_parent_secrets_and_unknowns() -> None:
    parent = {
        "HOME": "/home/agent",
        "PATH": "/usr/bin",
        "LANG": "C.UTF-8",
        "SKELETON_LLM_MODEL": "model:test",
        "OPENROUTER_API_KEY": "secret-a",
        "BWS_ACCESS_TOKEN": "secret-b",
        "SKELETON_TG_BOT": "secret-c",
        "UNRELATED_PARENT_FLAG": "drop-me",
    }
    registry = LaneEnvironmentPolicyRegistry({"lane:validation": validation_policy()})
    plan = registry.build(lane_id="lane:validation", parent_environment=parent, injected_environment={})
    assert dict(plan.environment) == {
        "HOME": "/home/agent",
        "PATH": "/usr/bin",
        "LANG": "C.UTF-8",
        "SKELETON_LLM_MODEL": "model:test",
    }
    assert plan.receipt.stripped_parent_key_count == 4
    assert plan.receipt.values_exposed is False


def test_secret_injection_is_exact_lane_scoped_and_receipt_is_value_free() -> None:
    policy = LaneEnvironmentPolicy(
        lane_id="lane:provider",
        inherit_keys=("HOME", "PATH"),
        inject_keys=("OPENROUTER_API_KEY",),
        denied_keys=(),
        secret_keys=("OPENROUTER_API_KEY",),
    )
    registry = LaneEnvironmentPolicyRegistry({"lane:provider": policy})
    plan = registry.build(
        lane_id="lane:provider",
        parent_environment={"HOME": "/home/agent", "PATH": "/usr/bin", "OPENROUTER_API_KEY": "parent-secret"},
        injected_environment={"OPENROUTER_API_KEY": "injected-secret"},
    )
    assert plan.environment["OPENROUTER_API_KEY"] == "injected-secret"
    receipt = asdict(plan.receipt)
    assert receipt["secret_key_names"] == ("OPENROUTER_API_KEY",)
    assert "injected-secret" not in repr(receipt)
    assert "parent-secret" not in repr(receipt)
    assert "injected-secret" not in repr(plan)


def test_undeclared_or_denied_injection_fails_closed() -> None:
    with pytest.raises(EnvironmentContractError, match="ENV_UNDECLARED_INJECTION"):
        LaneEnvironmentPolicyRegistry({"lane:validation": validation_policy()}).build(
            lane_id="lane:validation", parent_environment={}, injected_environment={"RANDOM_INJECT": "value"}
        )
    policy = LaneEnvironmentPolicy("lane:test", (), (), ("SAFE_FLAG",), ())
    with pytest.raises(EnvironmentContractError, match="ENV_DENIED_INJECTION"):
        LaneEnvironmentPolicyRegistry({"lane:test": policy}).build(lane_id="lane:test", parent_environment={}, injected_environment={"SAFE_FLAG": "1"})


def test_sensitive_parent_inheritance_is_forbidden_even_when_named() -> None:
    for key in ("OPENROUTER_API_KEY", "BWS_ACCESS_TOKEN", "MY_CUSTOM_SECRET", "SERVICE_PASSWORD"):
        policy = LaneEnvironmentPolicy("lane:test", (key,), (), (), ())
        with pytest.raises(EnvironmentContractError, match="ENV_POLICY_SECRET_INHERIT_FORBIDDEN"):
            validate_lane_environment_policy(policy)


def test_sensitive_injection_must_be_classified_as_secret() -> None:
    policy = LaneEnvironmentPolicy("lane:test", (), ("SKELETON_TG_BOT",), (), ())
    with pytest.raises(EnvironmentContractError, match="ENV_POLICY_SECRET_CLASSIFICATION_REQUIRED"):
        validate_lane_environment_policy(policy)


def test_overlap_duplicate_wildcard_and_invalid_lane_fail_closed() -> None:
    cases = [
        (LaneEnvironmentPolicy("lane:test", ("PATH",), ("PATH",), (), ()), "ENV_POLICY_SOURCE_OVERLAP"),
        (LaneEnvironmentPolicy("lane:test", ("PATH",), (), ("PATH",), ()), "ENV_POLICY_DENY_OVERLAP"),
        (LaneEnvironmentPolicy("lane:test", ("PATH", "PATH"), (), (), ()), "ENV_POLICY_DUPLICATE_KEY"),
        (LaneEnvironmentPolicy("lane:test", ("*",), (), (), ()), "ENV_POLICY_KEY_INVALID"),
        (LaneEnvironmentPolicy("validation", ("PATH",), (), (), ()), "ENV_LANE_ID_INVALID"),
    ]
    for policy, reason in cases:
        with pytest.raises(EnvironmentContractError, match=reason):
            validate_lane_environment_policy(policy)


def test_secret_key_must_be_in_injection_set() -> None:
    policy = LaneEnvironmentPolicy("lane:test", (), (), (), ("OPENROUTER_API_KEY",))
    with pytest.raises(EnvironmentContractError, match="ENV_POLICY_SECRET_MUST_BE_INJECTED"):
        validate_lane_environment_policy(policy)


def test_policy_and_receipt_schemas_are_closed_and_value_free() -> None:
    policy_schema = json.loads((ROOT / "schemas" / "runner_lane_environment_policy.schema.json").read_text())
    receipt_schema = json.loads((ROOT / "schemas" / "runner_child_environment_receipt.schema.json").read_text())
    assert policy_schema["additionalProperties"] is False
    assert receipt_schema["additionalProperties"] is False
    policy_payload = {
        "schema": "skeleton.runner_lane_environment_policy.v1",
        "lane_id": "lane:validation",
        "inherit_keys": ["HOME", "PATH"],
        "inject_keys": [],
        "denied_keys": ["OPENROUTER_API_KEY"],
        "secret_keys": [],
    }
    jsonschema.validate(policy_payload, policy_schema)
    receipt_payload = {
        "schema": "skeleton.runner_child_environment_receipt.v1",
        "lane_id": "lane:validation",
        "policy_hash": lane_environment_policy_hash(validation_policy()),
        "inherited_keys": ["HOME", "PATH"],
        "injected_keys": [],
        "secret_key_names": [],
        "stripped_parent_key_count": 3,
        "environment_key_count": 2,
        "values_exposed": False,
    }
    jsonschema.validate(receipt_payload, receipt_schema)
    assert receipt_schema["properties"]["values_exposed"]["const"] is False
    assert "environment" not in receipt_schema["properties"]
    assert "secret_values" not in receipt_schema["properties"]


def test_registry_binds_policy_to_exact_lane_and_rejects_unknown_lane() -> None:
    registry = LaneEnvironmentPolicyRegistry({"lane:validation": validation_policy()})
    with pytest.raises(EnvironmentContractError, match="ENV_POLICY_LANE_UNKNOWN"):
        registry.build(lane_id="lane:provider", parent_environment={}, injected_environment={})
    with pytest.raises(EnvironmentContractError, match="ENV_POLICY_REGISTRY_LANE_MISMATCH"):
        LaneEnvironmentPolicyRegistry({"lane:wrong": validation_policy()})


def test_receipt_binds_exact_policy_hash() -> None:
    policy = validation_policy()
    plan = LaneEnvironmentPolicyRegistry({policy.lane_id: policy}).build(
        lane_id=policy.lane_id, parent_environment={"HOME":"/h","PATH":"/p"}, injected_environment={}
    )
    assert plan.receipt.policy_hash == lane_environment_policy_hash(policy)
    changed = LaneEnvironmentPolicy(policy.lane_id, ("HOME",), (), policy.denied_keys, ())
    assert lane_environment_policy_hash(changed) != plan.receipt.policy_hash


@pytest.mark.xfail(
    strict=True,
    reason=(
        "NEEDS_OPERATOR: runner_vnext_readonly_preflight does not enforce the "
        "requested fail-closed minimum root free disk threshold yet."
    ),
)
def test_runner_vnext_readonly_preflight_fails_closed_on_insufficient_root_disk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workdir, ledger, lease = _write_readonly_preflight_fixture(tmp_path)
    monkeypatch.setenv(runner.RUNNER_VNEXT_STATE_ROOT_ENV, str(ledger.parent))
    monkeypatch.setenv(runner.RUNNER_VNEXT_LEDGER_DB_ENV, str(ledger))
    monkeypatch.setenv(runner.RUNNER_VNEXT_LEASE_DB_ENV, str(lease))
    monkeypatch.setattr(runner, "run_command", _healthy_readonly_probe)
    monkeypatch.setattr(
        runner.shutil,
        "disk_usage",
        lambda _path: (2 * 1024**3, 768 * 1024**2, 768 * 1024**2),
    )

    report = runner.runner_vnext_readonly_preflight(workdir)

    assert runner.maintenance_report_status(report) == "BLOCKED"
    assert "insufficient" in report.lower() or "disk" in report.lower()


def test_validation_temp_cleanup_refuses_non_runner_directory_without_deleting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent = tmp_path / "synthetic-temp"
    parent.mkdir()
    unsafe = parent / "ordinary-directory"
    unsafe.mkdir()
    marker = unsafe / "keep.txt"
    marker.write_text("synthetic\n")
    monkeypatch.setattr(runner.tempfile, "tempdir", str(parent))

    with pytest.raises(RuntimeError, match="validation_temp_cleanup_unsafe"):
        runner._remove_validation_pytest_temp_root(unsafe)

    assert unsafe.is_dir()
    assert marker.read_text() == "synthetic\n"


def test_runner_vnext_preflight_reports_stale_attestation_without_mutating(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    attestation = _write_attestation(state, observed=100.0, expires=120.0)
    before = attestation.read_text()

    status, fresh = runner._runner_vnext_preflight_attestation(state, now=121.0)

    assert (status, fresh) == ("stale", False)
    assert attestation.read_text() == before


def test_runner_vnext_preflight_rejects_mismatched_attestation_digest(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    attestation = _write_attestation(state, observed=100.0, expires=120.0)
    payload = json.loads(attestation.read_text())
    payload["snapshot"]["generation"] = 2
    attestation.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    before = attestation.read_text()

    with pytest.raises(RuntimeError, match="preflight_attestation_digest_invalid"):
        runner._runner_vnext_preflight_attestation(state, now=101.0)

    assert attestation.read_text() == before
