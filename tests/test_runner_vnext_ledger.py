from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from core.runner_vnext_ledger import LedgerError, OperationIdentity, OperationLedger

ROOT = Path(__file__).resolve().parents[1]


def identity(state: str = "git:a") -> OperationIdentity:
    return OperationIdentity("op-1", "idem-1", state)


def test_reserve_is_idempotent_only_for_exact_identity() -> None:
    ledger = OperationLedger()
    first = ledger.reserve(identity(), fence_token=3, external_dedupe_nonce="nonce-1")
    same = ledger.reserve(identity(), fence_token=99, external_dedupe_nonce="nonce-1")
    assert same.sequence == first.sequence
    assert same.fence_token == 3
    with pytest.raises(LedgerError, match="IDEMPOTENCY_DEDUPE_NONCE_MISMATCH"):
        ledger.reserve(identity(), fence_token=99, external_dedupe_nonce="nonce-2")
    with pytest.raises(LedgerError, match="IDEMPOTENCY_TARGET_STATE_MISMATCH"):
        ledger.reserve(identity("git:b"), fence_token=4)


def test_crash_leaves_recoverable_reserved_state_and_rebinds_fence(tmp_path) -> None:
    db = tmp_path / "ledger.sqlite"
    first = OperationLedger(db)
    first.reserve(identity(), fence_token=5)
    first.close()
    reopened = OperationLedger(db)
    assert reopened.status("idem-1") == "RESERVED"
    rebound = reopened.rebind_fence(identity(), expected_fence_token=5, new_fence_token=6)
    assert rebound.event_type == "FENCE_REBOUND"
    assert rebound.fence_token == 6
    assert [event.event_type for event in reopened.history("idem-1")] == ["RESERVED", "FENCE_REBOUND"]


def test_stale_fence_cannot_finish_after_rebind() -> None:
    ledger = OperationLedger()
    ledger.reserve(identity(), fence_token=1)
    ledger.rebind_fence(identity(), expected_fence_token=1, new_fence_token=2)
    with pytest.raises(LedgerError, match="STALE_FENCE_TOKEN"):
        ledger.finish(identity(), fence_token=1, success=True, reason_code="PASS", touched_resource_refs=("repo:file",), before_state_ref="sha:a", after_state_ref="sha:b", validation_status="PASS")
    done = ledger.finish(identity(), fence_token=2, success=True, reason_code="PASS", touched_resource_refs=("repo:file",), before_state_ref="sha:a", after_state_ref="sha:b", validation_status="PASS")
    assert done.event_type == "COMPLETED"


def test_terminal_receipt_is_immutable_and_idempotent_for_exact_terminal() -> None:
    ledger = OperationLedger()
    ledger.reserve(identity(), fence_token=7)
    first = ledger.finish(identity(), fence_token=7, success=False, reason_code="VALIDATION_FAILED", touched_resource_refs=("repo:file",), before_state_ref="sha:a", after_state_ref="sha:a", validation_status="FAIL")
    same = ledger.finish(identity(), fence_token=7, success=False, reason_code="VALIDATION_FAILED", touched_resource_refs=("repo:file",), before_state_ref="sha:a", after_state_ref="sha:a", validation_status="FAIL")
    assert same.sequence == first.sequence
    with pytest.raises(LedgerError, match="TERMINAL_RECEIPT_MISMATCH"):
        ledger.finish(identity(), fence_token=7, success=False, reason_code="OTHER", touched_resource_refs=("repo:other",), before_state_ref="sha:x", after_state_ref="sha:y", validation_status="FAIL")
    with pytest.raises(LedgerError, match="TERMINAL_RECEIPT_MISMATCH"):
        ledger.finish(identity(), fence_token=7, success=True, reason_code="PASS", touched_resource_refs=("repo:file",), before_state_ref="sha:a", after_state_ref="sha:b", validation_status="PASS")


def test_public_receipt_rejects_private_path_like_refs() -> None:
    ledger = OperationLedger()
    ledger.reserve(identity(), fence_token=1)
    with pytest.raises(LedgerError, match="PUBLIC_RESOURCE_REF_REQUIRED"):
        ledger.finish(identity(), fence_token=1, success=True, reason_code="PASS", touched_resource_refs=("/private/path",), before_state_ref="sha:a", after_state_ref="sha:b", validation_status="PASS")


def test_fence_rebind_must_be_monotonic_and_exact() -> None:
    ledger = OperationLedger()
    ledger.reserve(identity(), fence_token=4)
    with pytest.raises(LedgerError, match="STALE_FENCE_TOKEN"):
        ledger.rebind_fence(identity(), expected_fence_token=3, new_fence_token=5)
    with pytest.raises(LedgerError, match="FENCE_TOKEN_NOT_MONOTONIC"):
        ledger.rebind_fence(identity(), expected_fence_token=4, new_fence_token=4)


def test_ledger_event_schema_is_closed_and_append_only_shaped() -> None:
    schema = json.loads((ROOT / "schemas" / "runner_operation_ledger_event.schema.json").read_text())
    assert schema["additionalProperties"] is False
    assert schema["properties"]["event_type"]["enum"] == ["RESERVED", "FENCE_REBOUND", "COMPLETED", "FAILED"]
    event = {
        "schema":"skeleton.runner_operation_ledger_event.v1", "sequence":1,
        "operation_id":"op", "idempotency_key":"idem", "target_state_ref":"git:a",
        "event_type":"RESERVED", "fence_token":1, "reason_code":"OPERATION_RESERVED",
        "touched_resource_refs":[], "before_state_ref":None, "after_state_ref":None,
        "validation_status":None, "external_dedupe_nonce":"nonce",
    }
    jsonschema.validate(event, schema)


def test_terminal_replay_requires_exact_receipt_payload() -> None:
    ledger = OperationLedger()
    ident = identity()
    ledger.reserve(ident, fence_token=3)
    first = ledger.finish(ident, fence_token=3, success=True, reason_code="VALIDATION_PASS",
                          touched_resource_refs=("repo:file.py",), before_state_ref="sha256:a",
                          after_state_ref="sha256:b", validation_status="PASS")
    same = ledger.finish(ident, fence_token=3, success=True, reason_code="VALIDATION_PASS",
                         touched_resource_refs=("repo:file.py",), before_state_ref="sha256:a",
                         after_state_ref="sha256:b", validation_status="PASS")
    assert same.sequence == first.sequence
    with pytest.raises(LedgerError, match="TERMINAL_RECEIPT_MISMATCH"):
        ledger.finish(ident, fence_token=3, success=True, reason_code="VALIDATION_PASS",
                      touched_resource_refs=("repo:other.py",), before_state_ref="sha256:a",
                      after_state_ref="sha256:b", validation_status="PASS")


def test_state_refs_and_target_state_cannot_leak_private_paths() -> None:
    ledger = OperationLedger()
    with pytest.raises(LedgerError, match="PUBLIC_RESOURCE_REF_REQUIRED"):
        ledger.reserve(OperationIdentity("op", "idem-private", "/private/state"), fence_token=1)
    ident = identity()
    ledger.reserve(ident, fence_token=2)
    with pytest.raises(LedgerError, match="PUBLIC_RESOURCE_REF_REQUIRED"):
        ledger.finish(ident, fence_token=2, success=True, reason_code="VALIDATION_PASS",
                      touched_resource_refs=("repo:file.py",), before_state_ref="/private/before",
                      after_state_ref="sha256:b", validation_status="PASS")
