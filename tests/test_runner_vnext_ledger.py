from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from core.runner_vnext_ledger import LedgerError, OperationIdentity, OperationLedger

SCOPE = "a" * 64

ROOT = Path(__file__).resolve().parents[1]


def identity(state: str = "git:a") -> OperationIdentity:
    return OperationIdentity("op-1", "idem-1", state)


def test_reserve_is_idempotent_only_for_exact_identity() -> None:
    ledger = OperationLedger()
    first = ledger.reserve(identity(), fence_token=3, reservation_scope_hash=SCOPE, external_dedupe_nonce="nonce-1")
    same = ledger.reserve(identity(), fence_token=99, reservation_scope_hash=SCOPE, external_dedupe_nonce="nonce-1")
    assert same.sequence == first.sequence
    assert same.fence_token == 3
    with pytest.raises(LedgerError, match="IDEMPOTENCY_DEDUPE_NONCE_MISMATCH"):
        ledger.reserve(identity(), fence_token=99, reservation_scope_hash=SCOPE, external_dedupe_nonce="nonce-2")
    with pytest.raises(LedgerError, match="IDEMPOTENCY_TARGET_STATE_MISMATCH"):
        ledger.reserve(identity("git:b"), fence_token=4, reservation_scope_hash=SCOPE)


def test_crash_leaves_recoverable_reserved_state_and_rebinds_fence(tmp_path) -> None:
    db = tmp_path / "ledger.sqlite"
    first = OperationLedger(db)
    first.reserve(identity(), fence_token=5, reservation_scope_hash=SCOPE)
    first.close()
    reopened = OperationLedger(db)
    assert reopened.status("idem-1") == "RESERVED"
    rebound = reopened.rebind_fence(identity(), expected_fence_token=5, new_fence_token=6)
    assert rebound.event_type == "FENCE_REBOUND"
    assert rebound.fence_token == 6
    assert reopened.current_fence_token("idem-1") == 6
    assert [event.event_type for event in reopened.history("idem-1")] == ["RESERVED", "FENCE_REBOUND"]


def test_stale_fence_cannot_finish_after_rebind() -> None:
    ledger = OperationLedger()
    ledger.reserve(identity(), fence_token=1, reservation_scope_hash=SCOPE)
    ledger.rebind_fence(identity(), expected_fence_token=1, new_fence_token=2)
    with pytest.raises(LedgerError, match="STALE_FENCE_TOKEN"):
        ledger.finish(identity(), fence_token=1, success=True, reason_code="PASS", touched_resource_refs=("repo:file",), before_state_ref="sha:a", after_state_ref="sha:b", validation_status="PASS")
    done = ledger.finish(identity(), fence_token=2, success=True, reason_code="PASS", touched_resource_refs=("repo:file",), before_state_ref="sha:a", after_state_ref="sha:b", validation_status="PASS")
    assert done.event_type == "COMPLETED"


def test_terminal_receipt_is_immutable_and_idempotent_for_exact_terminal() -> None:
    ledger = OperationLedger()
    ledger.reserve(identity(), fence_token=7, reservation_scope_hash=SCOPE)
    first = ledger.finish(identity(), fence_token=7, success=False, reason_code="VALIDATION_FAILED", touched_resource_refs=("repo:file",), before_state_ref="sha:a", after_state_ref="sha:a", validation_status="FAIL")
    same = ledger.finish(identity(), fence_token=7, success=False, reason_code="VALIDATION_FAILED", touched_resource_refs=("repo:file",), before_state_ref="sha:a", after_state_ref="sha:a", validation_status="FAIL")
    assert same.sequence == first.sequence
    with pytest.raises(LedgerError, match="TERMINAL_RECEIPT_MISMATCH"):
        ledger.finish(identity(), fence_token=7, success=False, reason_code="OTHER", touched_resource_refs=("repo:other",), before_state_ref="sha:x", after_state_ref="sha:y", validation_status="FAIL")
    with pytest.raises(LedgerError, match="TERMINAL_RECEIPT_MISMATCH"):
        ledger.finish(identity(), fence_token=7, success=True, reason_code="PASS", touched_resource_refs=("repo:file",), before_state_ref="sha:a", after_state_ref="sha:b", validation_status="PASS")


def test_public_receipt_rejects_private_path_like_refs() -> None:
    ledger = OperationLedger()
    ledger.reserve(identity(), fence_token=1, reservation_scope_hash=SCOPE)
    with pytest.raises(LedgerError, match="PUBLIC_RESOURCE_REF_REQUIRED"):
        ledger.finish(identity(), fence_token=1, success=True, reason_code="PASS", touched_resource_refs=("/private/path",), before_state_ref="sha:a", after_state_ref="sha:b", validation_status="PASS")


def test_fence_rebind_must_be_monotonic_and_exact() -> None:
    ledger = OperationLedger()
    ledger.reserve(identity(), fence_token=4, reservation_scope_hash=SCOPE)
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
        "validation_status":None, "external_dedupe_nonce":"nonce", "reservation_scope_hash":SCOPE,
    }
    jsonschema.validate(event, schema)


def test_terminal_replay_requires_exact_receipt_payload() -> None:
    ledger = OperationLedger()
    ident = identity()
    ledger.reserve(ident, fence_token=3, reservation_scope_hash=SCOPE)
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
        ledger.reserve(OperationIdentity("op", "idem-private", "/private/state"), fence_token=1, reservation_scope_hash=SCOPE)
    ident = identity()
    ledger.reserve(ident, fence_token=2, reservation_scope_hash=SCOPE)
    with pytest.raises(LedgerError, match="PUBLIC_RESOURCE_REF_REQUIRED"):
        ledger.finish(ident, fence_token=2, success=True, reason_code="VALIDATION_PASS",
                      touched_resource_refs=("repo:file.py",), before_state_ref="/private/before",
                      after_state_ref="sha256:b", validation_status="PASS")


def test_reservation_scope_hash_is_authoritative_and_persistent(tmp_path) -> None:
    db = tmp_path / "ledger-scope.sqlite"
    ledger = OperationLedger(db)
    first = ledger.reserve(identity(), fence_token=5, reservation_scope_hash=SCOPE)
    assert first.reservation_scope_hash == SCOPE
    with pytest.raises(LedgerError, match="IDEMPOTENCY_SCOPE_HASH_MISMATCH"):
        ledger.reserve(identity(), fence_token=5, reservation_scope_hash="b" * 64)
    ledger.close()
    reopened = OperationLedger(db)
    assert reopened.reservation_event("idem-1").reservation_scope_hash == SCOPE
    rebound = reopened.rebind_fence(identity(), expected_fence_token=5, new_fence_token=6)
    assert rebound.reservation_scope_hash == SCOPE


def test_effect_start_marker_is_durable_and_blocks_fence_rebind(tmp_path) -> None:
    db = tmp_path / "started-ledger.sqlite"
    ident = identity()
    first = OperationLedger(db)
    first.reserve(ident, fence_token=5, reservation_scope_hash=SCOPE)
    started = first.mark_started(ident, fence_token=5, execution_grant_hash="b" * 64)
    assert started.execution_grant_hash == "b" * 64
    assert first.status("idem-1") == "STARTED"
    first.close()
    reopened = OperationLedger(db)
    persisted = reopened.started_event("idem-1")
    assert persisted.execution_grant_hash == "b" * 64
    assert reopened.status("idem-1") == "STARTED"
    with pytest.raises(LedgerError, match="OPERATION_STARTED_FENCE_IMMUTABLE"):
        reopened.rebind_fence(ident, expected_fence_token=5, new_fence_token=6)


def test_effect_start_marker_is_exactly_bound_and_idempotent() -> None:
    ledger = OperationLedger()
    ident = identity()
    ledger.reserve(ident, fence_token=3, reservation_scope_hash=SCOPE)
    first = ledger.mark_started(ident, fence_token=3, execution_grant_hash="c" * 64)
    same = ledger.mark_started(ident, fence_token=3, execution_grant_hash="c" * 64)
    assert same == first
    with pytest.raises(LedgerError, match="EXECUTION_START_EVIDENCE_MISMATCH"):
        ledger.mark_started(ident, fence_token=3, execution_grant_hash="d" * 64)
