from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import pytest
from jsonschema.exceptions import ValidationError
from jsonschema import validate

from core.home_edge.executor import HomeEdgeExecReceipt, HomeEdgeExecRequest, sign_request
from core.home_edge import remote_windows_audit as audit


SECRET = "remote-windows-audit-secret"
PUBLIC_KEY = "ssh-ed25519 " + "A" * 48 + " controller"
FINGERPRINT = "SHA256:" + "A" * 32


def enrollment(**updates: object) -> dict[str, object]:
    data: dict[str, object] = {
        "schema": audit.ENROLLMENT_SCHEMA,
        "node_id": audit.TARGET_NODE,
        "audit_user": audit.AUDIT_USER,
        "transport": "tailscale_openssh",
        "machine_identity_public_key": PUBLIC_KEY,
        "machine_identity_fingerprint": FINGERPRINT,
        "registered_at": datetime.now(UTC).isoformat(),
    }
    data.update(updates)
    return data


def evidence(**updates: object) -> dict[str, object]:
    data: dict[str, object] = {
        "schema": "skeleton.home_edge.remote_windows_audit.private_evidence.v1",
        "operation": audit.OPERATION,
        "collected_at": datetime.now(UTC).isoformat(),
        "machine_identity_fingerprint": FINGERPRINT,
        "os": {
            "caption": "Microsoft Windows 11 Pro",
            "version": "10.0.22631",
            "build_number": "22631",
            "architecture": "64-bit",
        },
        "hardware": {
            "total_physical_memory": 16 * 1024**3,
            "processor": "Synthetic CPU",
            "cores": 8,
        },
        "storage": [{"drive": "C:", "size": 512 * 1024**3, "free": 128 * 1024**3}],
        "security": {
            "secure_boot": True,
            "tpm_present": True,
            "defender_enabled": True,
            "defender_realtime": True,
            "defender_signature_age_days": 1,
        },
        "remote_access": {
            "openssh_status": "Running",
            "tailscale_status": "Running",
            "tailscale_backend_state": "Running",
        },
        "collection_scope": ["Win32_OperatingSystem", "Get-MpComputerStatus"],
        "excluded_scope": ["personal_files", "messages", "browser_history", "photos"],
    }
    data.update(updates)
    return data


def receipt(stdout: str) -> HomeEdgeExecReceipt:
    now = datetime.now(UTC).isoformat()
    return HomeEdgeExecReceipt(
        status="ok",
        request_id="req",
        node_id=audit.TARGET_NODE,
        execution_lane="read_only",
        exit_code=0,
        stdout=stdout,
        stderr="",
        started_at=now,
        finished_at=now,
        duration_seconds=0.01,
        idempotency="executed",
        receipt_hash="f" * 64,
    )


def test_builds_fixed_signed_read_only_home_edge_request() -> None:
    parsed = audit.RemoteAuditEnrollment.from_mapping(enrollment())
    request = audit.build_read_only_system_audit_request(
        parsed,
        request_id="fixed",
        hmac_secret=SECRET,
    )
    home_edge_request = HomeEdgeExecRequest.from_mapping(request)

    assert home_edge_request.signature == sign_request(home_edge_request, SECRET)
    assert home_edge_request.node_id == audit.TARGET_NODE
    assert home_edge_request.execution_lane.value == "read_only"
    assert home_edge_request.run_as.value == "desktop-user"
    assert home_edge_request.argv[:5] == (
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
    )
    assert audit.OPERATION in home_edge_request.argv[-1]
    assert "Get-ChildItem" not in home_edge_request.argv[-1]
    assert "browser" in home_edge_request.argv[-1]


def test_current_main_workstation_transport_rejects_non_fixed_request(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = audit.RemoteAuditEnrollment.from_mapping(enrollment())
    request = audit.build_read_only_system_audit_request(
        parsed,
        request_id="fixed",
        hmac_secret=SECRET,
    )
    captured: list[Mapping[str, Any]] = []

    def fake_execute(outbound: Mapping[str, Any]) -> HomeEdgeExecReceipt:
        captured.append(outbound)
        return receipt(json.dumps(evidence()))

    transport = audit.CurrentMainWorkstationNodeTransport()
    monkeypatch.setattr(audit, "execute_home_edge_request", fake_execute)

    assert transport(request).status == "ok"
    with pytest.raises(Exception, match="fixed current-main audit operation"):
        transport({**request, "argv": ["powershell.exe", "-Command", "Get-ChildItem"]})
    with pytest.raises(Exception, match="idempotency"):
        transport({**request, "idempotency_key": "other"})
    with pytest.raises(Exception, match="signed current-main"):
        transport({key: value for key, value in request.items() if key != "signature"})

    assert captured == [request]


def test_rejects_behavior_changing_enrollment_fields() -> None:
    with pytest.raises(ValueError, match="node_id"):
        audit.RemoteAuditEnrollment.from_mapping(enrollment(node_id="private-host"))
    with pytest.raises(ValueError, match="audit_user"):
        audit.RemoteAuditEnrollment.from_mapping(enrollment(audit_user="Administrator"))
    with pytest.raises(ValueError, match="transport"):
        audit.RemoteAuditEnrollment.from_mapping(enrollment(transport="public_internet_ssh"))


def test_receipt_schema_and_public_private_boundary(tmp_path: Path) -> None:
    calls: list[Mapping[str, Any]] = []

    def transport(request: Mapping[str, Any]) -> HomeEdgeExecReceipt:
        calls.append(request)
        return receipt(json.dumps(evidence()))

    private_out = tmp_path / "private" / "evidence.json"
    public = audit.run_read_only_system_audit_v1(
        enrollment(),
        private_evidence_path=private_out,
        transport=transport,
        hmac_secret=SECRET,
    )

    schema = json.loads(Path("schemas/remote_windows_audit_receipt.schema.json").read_text(encoding="utf-8"))
    validate(public, schema)
    assert public["verdict"] == "KEEP"
    assert public["executor_receipt_hash"] == "f" * 64
    assert "machine_identity_public_key" not in json.dumps(public)
    assert "Synthetic CPU" not in json.dumps(public)
    assert private_out.exists()
    assert calls


def test_private_evidence_cannot_be_written_inside_repo() -> None:
    with pytest.raises(Exception, match="outside repository"):
        audit.run_read_only_system_audit_v1(
            enrollment(),
            private_evidence_path=Path("private-evidence.json"),
            transport=lambda _request: receipt(json.dumps(evidence())),
            hmac_secret=SECRET,
        )


def test_deterministic_verdicts_from_observed_evidence() -> None:
    assert audit.evaluate_windows_audit_evidence(evidence())["verdict"] == "KEEP"
    assert audit.evaluate_windows_audit_evidence(
        evidence(hardware={"total_physical_memory": 4 * 1024**3}, storage=[{"free": 128 * 1024**3}])
    )["verdict"] == "UPGRADE"
    assert audit.evaluate_windows_audit_evidence(
        evidence(os={"caption": "Microsoft Windows 7", "build_number": "7601", "architecture": "64-bit"})
    )["verdict"] == "RETIRE"
    assert audit.evaluate_windows_audit_evidence(
        evidence(security={"defender_enabled": False, "defender_realtime": False}, remote_access={"openssh_status": "Stopped"})
    )["verdict"] == "REPAIR"


def test_enrollment_schema_accepts_public_safe_record() -> None:
    schema = json.loads(Path("schemas/remote_audit_enrollment.schema.json").read_text(encoding="utf-8"))
    validate(enrollment(), schema)


def test_owner_enrollment_schema_accepts_private_https_endpoint_payload() -> None:
    schema = json.loads(Path("schemas/remote_windows_owner_enrollment.schema.json").read_text(encoding="utf-8"))
    payload = {
        "schema": "skeleton.home_edge.remote_windows_audit.owner_enrollment.v1",
        "enrollment_id": "win-target-01",
        "audit_user": audit.AUDIT_USER,
        "transport": "tailscale_openssh",
        "controller_public_key": PUBLIC_KEY,
        "token_lifecycle": "no_automatic_expiry_manual_rotation_or_successful_enrollment_only",
        "link_sha256": "a" * 64,
    }
    validate(payload, schema)

    with pytest.raises(ValidationError):
        validate({**payload, "expires_at": datetime.now(UTC).isoformat()}, schema)
