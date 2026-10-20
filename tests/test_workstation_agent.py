"""Synthetic only. No desktop, network, CAD application or real credentials involved."""
from dataclasses import replace
import json
import pytest

from core.workstation_agent import (
    ApplicationSession, CommandEnvelope, GateAuthorization, InteractionLease,
    LeaseMode, NodeIdentity, NodeSession, Trust, WorkstationAgent, WorkstationRejected,
)
from adapters.workstation_control import SyntheticWorkstationAdapter

NOW = 2_000_000_000


class SyntheticGate:
    def __init__(self, approved=True, wrong_operation=False):
        self.approved, self.wrong_operation = approved, wrong_operation
        self.calls = 0

    def authorize(self, cmd):
        self.calls += 1
        return GateAuthorization(self.approved, cmd.node_id, cmd.node_session_id,
                                 cmd.lease_id, cmd.command_id,
                                 "SNAPSHOT" if self.wrong_operation else cmd.operation,
                                 NOW + 600, "synthetic-test-approval")


def agent(mode=LeaseMode.APPLY_APPROVED, *, gate=None, trust=Trust.TRUSTED):
    identity = NodeIdentity("node-1", "install-1", trust)
    node = NodeSession("session-1", "node-1", NOW - 100, NOW + 1000)
    app = ApplicationSession("app-1", "session-1", "workspace-1", "synthetic", 0)
    lease = InteractionLease("lease-1", "app-1", "session-1", mode, NOW + 600)
    adapter = SyntheticWorkstationAdapter()
    return WorkstationAgent(identity=identity, node_session=node, application=app,
                            lease=lease, gate=gate, adapter=adapter, clock=lambda: NOW), adapter


def cmd(operation="SNAPSHOT", *, key="k1", payload=None, revision=0, **changes):
    opts = dict(command_id="cmd-1", idempotency_key=key, node_id="node-1",
                node_session_id="session-1", application_session_id="app-1",
                lease_id="lease-1", workspace_id="workspace-1", operation=operation,
                issued_at=NOW - 1, expires_at=NOW + 60,
                expected_revision=revision, payload=payload or {})
    opts.update(changes)
    return CommandEnvelope(**opts)


def test_missing_trusted_gate_fails_closed():
    a, db = agent()
    r = a.submit(cmd())
    assert r.status == "BLOCKED" and r.reason_class == "TRUSTED_GATE_MISSING"
    assert db.public_snapshot()["revision"] == 0


def test_readonly_working_and_no_data_exposed():
    gate = SyntheticGate()
    a, db = agent(LeaseMode.OBSERVE, gate=gate)
    r = a.submit(cmd("SNAPSHOT"))
    assert r.status == "APPLIED" and r.before_revision == r.after_revision == 0
    assert "payload" not in str(r.public()) and "workspace-1" not in str(r.public())
    denied = a.submit(cmd("APPLY", key="k2", payload={"entity_id":"secret", "properties":{"type":"wall"}}))
    assert denied.reason_class == "OBSERVE_LEASE_FORBIDS_MUTATION"
    assert gate.calls == 1
    assert db.public_snapshot()["object_count"] == 0


def test_gate_denied_and_bound_to_operation_and_command():
    a, db = agent(gate=SyntheticGate(approved=False))
    assert a.submit(cmd("APPLY", payload={"entity_id":"wall", "properties":{"x":1}})).reason_class == "GATE_DENIED"
    b, _ = agent(gate=SyntheticGate(wrong_operation=True))
    assert b.submit(cmd("APPLY", payload={"entity_id":"wall", "properties":{"x":1}})).reason_class == "GATE_BINDING_MISMATCH"
    assert db.public_snapshot()["object_count"] == 0


def test_synthetic_apply_idempotent_and_receipt_offline_queue():
    gate = SyntheticGate()
    a, db = agent(gate=gate)
    c = cmd("APPLY", payload={"entity_id":"wall", "properties":{"height":3}})
    applied = a.submit(c)
    assert applied.status == "APPLIED" and applied.after_revision == 1
    assert a.submit(c) == applied
    assert gate.calls == 1 and db.public_snapshot()["revision"] == 1
    assert a.submit(cmd("APPLY", payload={"entity_id":"wall", "properties":{"height":100}})).reason_class == "IDEMPOTENCY_CONFLICT"
    a.set_transport_online(False)
    assert a.submit(cmd("SNAPSHOT", key="other")).reason_class == "NODE_OFFLINE"
    queued = a.pending_receipts()
    assert len(queued) == 1 and queued[0]["receipt_hash"] == applied.receipt_hash
    assert a.ack_receipt(applied.receipt_hash) and not a.pending_receipts()


def test_stale_and_wrong_boundaries_and_revocation():
    a, _ = agent(gate=SyntheticGate())
    assert a.submit(cmd(revision=100)).reason_class == "STALE_APPLICATION_CONTEXT"
    assert a.submit(cmd(node_id="other-node")).reason_class == "COMMAND_BOUNDARY_MISMATCH"
    a.lease = replace(a.lease, revoked=True)
    assert a.submit(cmd()).reason_class == "LEASE_EXPIRED_OR_REVOKED"


def test_session_expiry_and_device_not_trusted():
    a, _ = agent(gate=SyntheticGate(), trust=Trust.REVOKED)
    assert a.submit(cmd()).reason_class == "DEVICE_NOT_TRUSTED"
    b, _ = agent(gate=SyntheticGate())
    b.node_session = replace(b.node_session, expires_at=NOW - 1)
    assert b.submit(cmd()).reason_class == "NODE_SESSION_EXPIRED"


def test_local_pause_abort_nonbypassable():
    a, _ = agent(gate=SyntheticGate())
    a.local_pause()
    assert a.submit(cmd()).reason_class == "LOCAL_PAUSE_OR_ABORT"
    a.local_abort()
    assert a.public_status()["aborted"]
    assert a.submit(cmd(key="other")).status == "BLOCKED"


def test_checkpoint_apply_and_rollback_simulated_only():
    a, db = agent(gate=SyntheticGate())
    assert a.submit(cmd("CHECKPOINT", payload={"checkpoint_id":"base"})).status == "APPLIED"
    a.submit(cmd("APPLY", key="apply", revision=0, payload={"entity_id":"wall", "properties":{"x":2}}))
    assert db.public_snapshot()["revision"] == 1
    assert a.submit(cmd("ROLLBACK", key="stale", revision=0, payload={"checkpoint_id":"base"})).reason_class == "STALE_APPLICATION_CONTEXT"
    assert a.submit(cmd("ROLLBACK", key="rollback", revision=1, payload={"checkpoint_id":"base"})).status == "APPLIED"
    assert db.public_snapshot()["object_count"] == 0 and db.public_snapshot()["revision"] == 2
    assert a.submit(cmd("SNAPSHOT", key="obsolete", revision=0)).reason_class == "STALE_APPLICATION_CONTEXT"


def test_invalid_command_payload_ttl_and_unknown_operation():
    with pytest.raises(WorkstationRejected):
        cmd("EXEC")
    with pytest.raises(WorkstationRejected):
        cmd(payload={"binary": object()})
    with pytest.raises(WorkstationRejected):
        cmd(expires_at=NOW+2000)
    with pytest.raises(WorkstationRejected):
        cmd(issued_at=float("nan"))
    with pytest.raises(WorkstationRejected):
        cmd(payload={"large": "x"*17000})


def test_different_command_id_same_replay_key_blocks():
    a, _ = agent(gate=SyntheticGate())
    a.submit(cmd())
    denied = a.submit(cmd(command_id="different"))
    assert denied.reason_class == "IDEMPOTENCY_CONFLICT"


def test_no_transport_or_privileged_executor_exposed():
    from core import workstation_agent as w
    for forbidden in ("subprocess", "socket", "requests", "ctypes", "win32com", "os.system"):
        assert forbidden not in w.__dict__
    public = agent(gate=SyntheticGate())[0].public_status()
    assert set(public).isdisjoint({"node_id", "workspace_id", "provider_id", "payload", "path"})


def test_adapter_blocks_extra_fields_and_live_code():
    g = SyntheticGate()
    a, db = agent(gate=g)
    r = a.submit(cmd("APPLY", payload={"entity_id": "wall", "properties": {"x": 5}, "command": "calc.exe"}))
    assert r.status == "BLOCKED" and r.reason_class == "ADAPTER_PAYLOAD_REJECTED"
    assert db.public_snapshot()["object_count"] == 0


def test_future_command_and_expired_lease():
    a, _ = agent(gate=SyntheticGate())
    assert a.submit(cmd(issued_at=NOW + 40, expires_at=NOW + 41)).reason_class == "COMMAND_EXPIRED_OR_FUTURE"
    a.lease = replace(a.lease, expires_at=NOW)
    assert a.submit(cmd()).reason_class == "LEASE_EXPIRED_OR_REVOKED"


def test_approval_failure_never_falls_back_to_other_provider():
    class BrokenGate:
        def authorize(self, command):
            raise RuntimeError("sensitive private details must not leak")
    a, _ = agent(gate=BrokenGate())
    receipt = a.submit(cmd())
    assert receipt.reason_class == "GATE_UNAVAILABLE"
    assert "sensitive" not in str(receipt.public())


def test_schema_contract_examples_if_jsonschema_present():
    try:
        import jsonschema
    except ImportError:
        pytest.skip("jsonschema optional dev dependency")
    from pathlib import Path
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "workstation_command.schema.json").read_text())
    example = cmd().__dict__ if hasattr(cmd(), "__dict__") else {
        name: getattr(cmd(), name) for name in cmd().__dataclass_fields__
    }
    jsonschema.validate(example, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**example, "operation": "EXEC"}, schema)
