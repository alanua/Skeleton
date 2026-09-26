from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.runner_poll_github_tasks as runner
from core import runner_controller_privileged_gateway as gateway


HEAD_SHA = "1e1b26341a43cce967d024c8c60b0061025a8b24"
OTHER_SHA = "2e1b26341a43cce967d024c8c60b0061025a8b24"
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")


def _body(expected_main_sha: str = HEAD_SHA) -> str:
    return "\n".join(
        (
            f"Mode: {runner.RUNTIME_MAINTENANCE_MODE}",
            f"Maintenance Task ID: {runner.SKELETON_CONTROL_MCP_HETZNER_ACTIVATE_V1}",
            f"Repository: {runner.REPO}",
            f"Expected Main SHA: {expected_main_sha}",
            "Target: runner-controller",
            f"Operator Approval: {gateway.SKELETON_CONTROL_MCP_HETZNER_OPERATOR_APPROVAL}",
        )
    )


def _patch_checkout(monkeypatch, sha: str = HEAD_SHA) -> None:
    registered = SimpleNamespace(
        checkout_path=gateway.CANONICAL_CHECKOUT_PATH,
        status_lines=[],
    )
    monkeypatch.setattr(
        runner, "_registered_skeleton_checkout", lambda task_id: (registered, None)
    )
    monkeypatch.setattr(runner, "_verify_skeleton_checkout_present", lambda *args: None)
    monkeypatch.setattr(runner, "_read_skeleton_origin", lambda *args: None)
    monkeypatch.setattr(runner, "_read_skeleton_current_branch", lambda *args: None)
    monkeypatch.setattr(runner, "_read_skeleton_clean_state", lambda *args: None)
    monkeypatch.setattr(runner, "_fetch_skeleton_origin_main", lambda *args: None)
    monkeypatch.setattr(runner, "_read_skeleton_sha", lambda *args: (sha, None))
    monkeypatch.setattr(
        runner,
        "_run_freshness_command",
        lambda *args: (f"{sha}\trefs/heads/main\n", None),
    )


def _receipt(reason: str) -> dict[str, object]:
    return {
        "schema": gateway.RECEIPT_SCHEMA_ID,
        "status": "NEEDS_OPERATOR",
        "reason": reason,
        "action_id": gateway.SKELETON_CONTROL_MCP_HETZNER_ACTIVATE_TASK_ID,
        "repository": runner.REPO,
        "target": "runner-controller",
        "request_hash": "d" * 64,
        "private_evidence_exposed": False,
        "stderr_exposed": False,
        "env_exposed": False,
        "private_paths_exposed": False,
        "mutation_started": False,
        "mutation_performed": False,
        "external_side_effects_executed": False,
    }


def _assert_gateway_token(token: str) -> None:
    assert TOKEN_RE.fullmatch(token) is not None
    assert len(token.encode("utf-8")) <= gateway.MAX_TOKEN_BYTES


def test_dispatch_identity_uses_nonce_and_exact_main_sha(monkeypatch):
    monkeypatch.setattr(runner.time, "time_ns", lambda: 123456789)
    same_a = runner._skeleton_control_mcp_hetzner_dispatch_identity(HEAD_SHA)
    same_b = runner._skeleton_control_mcp_hetzner_dispatch_identity(HEAD_SHA.upper())

    monkeypatch.setattr(runner.time, "time_ns", lambda: 123456789)
    other_sha = runner._skeleton_control_mcp_hetzner_dispatch_identity(OTHER_SHA)

    monkeypatch.setattr(runner.time, "time_ns", lambda: 987654321)
    other_nonce = runner._skeleton_control_mcp_hetzner_dispatch_identity(HEAD_SHA)

    assert same_a == same_b
    assert same_a != other_sha
    assert same_a != other_nonce
    for token in (*same_a, *other_sha, *other_nonce):
        _assert_gateway_token(token)
    assert "20260901" not in same_a[1]


def test_activation_request_uses_per_dispatch_identity_without_runtime_call(monkeypatch):
    _patch_checkout(monkeypatch)
    monkeypatch.setattr(runner.time, "time_ns", lambda: 123456789)
    captured: dict[str, object] = {}

    class FakeTransport:
        def submit(self, request):
            captured.update(request)
            return 0, json.dumps(_receipt("ACTION_NOT_REGISTERED")).encode("utf-8")

    monkeypatch.setattr(gateway, "LocalSudoGatewayTransport", FakeTransport)

    report = runner.skeleton_control_mcp_hetzner_activate_v1(_body())

    assert runner.maintenance_report_status(report) == "NEEDS_OPERATOR"
    assert captured["request_id"] == runner._skeleton_control_mcp_hetzner_dispatch_identity(
        HEAD_SHA
    )[0]
    assert captured[
        "idempotency_key"
    ] == runner._skeleton_control_mcp_hetzner_dispatch_identity(HEAD_SHA)[1]
    _assert_gateway_token(str(captured["request_id"]))
    _assert_gateway_token(str(captured["idempotency_key"]))
    assert captured["action_id"] == gateway.SKELETON_CONTROL_MCP_HETZNER_ACTIVATE_TASK_ID
    assert captured["operator_approval"] == gateway.SKELETON_CONTROL_MCP_HETZNER_OPERATOR_APPROVAL
    assert captured["expected_main_sha"] == HEAD_SHA
    assert captured["registered_clean_main_sha"] == HEAD_SHA
    assert captured["github_main_sha"] == HEAD_SHA
    assert captured["checkout_path"] == str(gateway.CANONICAL_CHECKOUT_PATH)


def test_current_replay_reasons_fail_closed_instead_of_receipt_invalid(monkeypatch):
    _patch_checkout(monkeypatch)
    reports: list[str] = []

    class FakeTransport:
        def __init__(self) -> None:
            self.reason = ("IDEMPOTENCY_KEY_CONFLICT", "PRIOR_EXECUTION_STATE_UNCERTAIN")[
                len(reports)
            ]

        def submit(self, request):
            return 0, json.dumps(_receipt(self.reason)).encode("utf-8")

    monkeypatch.setattr(gateway, "LocalSudoGatewayTransport", FakeTransport)

    for _ in range(2):
        reports.append(runner.skeleton_control_mcp_hetzner_activate_v1(_body()))

    for reason, report in zip(
        ("IDEMPOTENCY_KEY_CONFLICT", "PRIOR_EXECUTION_STATE_UNCERTAIN"), reports
    ):
        assert runner.maintenance_report_status(report) == "NEEDS_OPERATOR"
        assert "gateway_status=NEEDS_OPERATOR" in report
        assert f"reason={reason}" in report
        assert "reason=privileged_gateway_receipt_invalid" not in report
        assert "activation_executed=false" in report
        assert "external_side_effects_executed=false" in report


def test_zero_mutation_needs_operator_reason_surfaces_without_reason_allowlist(
    monkeypatch,
):
    _patch_checkout(monkeypatch)

    class FakeTransport:
        def submit(self, request):
            return 0, json.dumps(_receipt("BOUNDED_ZERO_MUTATION_GATE")).encode(
                "utf-8"
            )

    monkeypatch.setattr(gateway, "LocalSudoGatewayTransport", FakeTransport)

    report = runner.skeleton_control_mcp_hetzner_activate_v1(_body())

    assert runner.maintenance_report_status(report) == "NEEDS_OPERATOR"
    assert "gateway_status=NEEDS_OPERATOR" in report
    assert "reason=BOUNDED_ZERO_MUTATION_GATE" in report
    assert "reason=privileged_gateway_receipt_invalid" not in report
    assert "activation_executed=false" in report
    assert "external_side_effects_executed=false" in report


def test_post_dispatch_needs_operator_receipt_surfaces_when_mutation_flags_cohere(
    monkeypatch,
):
    _patch_checkout(monkeypatch)
    receipt = _receipt("BOUNDED_POST_DISPATCH_GATE")
    receipt.update(
        {
            "mutation_started": True,
            "mutation_performed": True,
            "external_side_effects_executed": True,
            "expected_main_sha": HEAD_SHA,
            "source_blob": gateway.SKELETON_CONTROL_MCP_HETZNER_SOURCE_BLOB,
            "protected_copy_verified": True,
            "installed_artifacts_verified": False,
            "activation_executed": False,
        }
    )

    class FakeTransport:
        def submit(self, request):
            return 0, json.dumps(receipt).encode("utf-8")

    monkeypatch.setattr(gateway, "LocalSudoGatewayTransport", FakeTransport)

    report = runner.skeleton_control_mcp_hetzner_activate_v1(_body())

    assert runner.maintenance_report_status(report) == "NEEDS_OPERATOR"
    assert "gateway_status=NEEDS_OPERATOR" in report
    assert "reason=BOUNDED_POST_DISPATCH_GATE" in report
    assert "source_blob=" + gateway.SKELETON_CONTROL_MCP_HETZNER_SOURCE_BLOB in report
    assert "protected_copy_verified=true" in report
    assert "installed_artifacts_verified=false" in report
    assert "mutation_started=true" in report
    assert "mutation_performed=true" in report
    assert "activation_executed=false" in report
    assert "external_side_effects_executed=true" in report


def test_post_dispatch_needs_operator_rejects_wrong_well_formed_installer_sha(
    monkeypatch,
):
    _patch_checkout(monkeypatch)
    receipt = _receipt("BOUNDED_POST_DISPATCH_GATE")
    receipt.update(
        {
            "mutation_started": True,
            "mutation_performed": True,
            "external_side_effects_executed": True,
            "expected_main_sha": HEAD_SHA,
            "source_blob": gateway.SKELETON_CONTROL_MCP_HETZNER_SOURCE_BLOB,
            "installer_sha256": "a" * 64,
            "protected_copy_verified": True,
            "installed_artifacts_verified": False,
            "activation_executed": False,
        }
    )

    class FakeTransport:
        def submit(self, request):
            return 0, json.dumps(receipt).encode("utf-8")

    monkeypatch.setattr(gateway, "LocalSudoGatewayTransport", FakeTransport)

    report = runner.skeleton_control_mcp_hetzner_activate_v1(_body())

    assert runner.maintenance_report_status(report) == "BLOCKED"
    assert "reason=privileged_gateway_receipt_invalid" in report


def test_done_receipt_requires_exact_installer_and_hardened_success_state(monkeypatch):
    _patch_checkout(monkeypatch)
    valid = _receipt("SKELETON_CONTROL_MCP_HETZNER_LAUNCHER_VERIFIED")
    valid.update(
        {
            "status": "DONE",
            "mutation_started": True,
            "mutation_performed": True,
            "external_side_effects_executed": True,
            "expected_main_sha": HEAD_SHA,
            "source_blob": gateway.SKELETON_CONTROL_MCP_HETZNER_SOURCE_BLOB,
            "installer_sha256": gateway.SKELETON_CONTROL_MCP_HETZNER_SOURCE_SHA256,
            "protected_copy_verified": True,
            "installed_artifacts_verified": True,
            "activation_executed": False,
        }
    )
    wrong_hash = dict(valid)
    wrong_hash["installer_sha256"] = "a" * 64
    missing_hash = dict(valid)
    missing_hash.pop("installer_sha256")
    wrong_flags = dict(valid)
    wrong_flags["mutation_performed"] = False
    wrong_flags["external_side_effects_executed"] = False
    receipts = [valid, wrong_hash, missing_hash, wrong_flags]

    class FakeTransport:
        def submit(self, request):
            return 0, json.dumps(receipts.pop(0)).encode("utf-8")

    monkeypatch.setattr(gateway, "LocalSudoGatewayTransport", FakeTransport)

    report = runner.skeleton_control_mcp_hetzner_activate_v1(_body())
    assert runner.maintenance_report_status(report) == "DONE"
    assert (
        "installer_sha256=" + gateway.SKELETON_CONTROL_MCP_HETZNER_SOURCE_SHA256
        in report
    )

    for _ in range(3):
        report = runner.skeleton_control_mcp_hetzner_activate_v1(_body())
        assert runner.maintenance_report_status(report) == "BLOCKED"
        assert "reason=privileged_gateway_receipt_invalid" in report


def test_needs_operator_receipt_requires_false_zero_mutation_flags(monkeypatch):
    _patch_checkout(monkeypatch)
    receipts = []
    for key in (
        "mutation_started",
        "mutation_performed",
        "external_side_effects_executed",
    ):
        for value in (True, None, "false"):
            receipt = _receipt("BOUNDED_ZERO_MUTATION_GATE")
            receipt[key] = value
            receipts.append(receipt)
        receipt = _receipt("BOUNDED_ZERO_MUTATION_GATE")
        receipt.pop(key)
        receipts.append(receipt)
    receipt = _receipt("BOUNDED_POST_DISPATCH_GATE")
    receipt.update(
        {
            "mutation_started": True,
            "mutation_performed": True,
            "external_side_effects_executed": True,
            "expected_main_sha": HEAD_SHA,
            "source_blob": gateway.SKELETON_CONTROL_MCP_HETZNER_SOURCE_BLOB,
            "installed_artifacts_verified": True,
            "activation_executed": False,
        }
    )
    receipts.append(receipt)

    class FakeTransport:
        def submit(self, request):
            return 0, json.dumps(receipts.pop(0)).encode("utf-8")

    monkeypatch.setattr(gateway, "LocalSudoGatewayTransport", FakeTransport)

    for _ in range(len(receipts)):
        report = runner.skeleton_control_mcp_hetzner_activate_v1(_body())
        assert runner.maintenance_report_status(report) == "BLOCKED"
        assert "reason=privileged_gateway_receipt_invalid" in report
