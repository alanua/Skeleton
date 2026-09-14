from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path

import pytest

import scripts.runner_vnext_poll as poll


@dataclass
class _Source:
    repository: str = "alanua/Skeleton"


@dataclass
class _Item:
    issue: dict
    source: _Source = field(default_factory=_Source)

    @property
    def source_repository(self) -> str:
        return self.source.repository

    @property
    def issue_number(self) -> int:
        return int(self.issue["number"])


def _diagnostic_body(*, sha: str = "a" * 40, idempotency: str = "diag-key") -> str:
    return f"""Mode: {poll.DIAGNOSTIC_MODE}
Repository: alanua/Skeleton
Expected Main SHA: {sha}
Profile: {poll.EXTERNAL_ATTESTATION_PROFILE}
Idempotency Key: {idempotency}
"""


def _diagnostic_bound(*, issue_number: int = 42, sha: str = "a" * 40, idempotency: str = "diag-key"):
    return poll._diagnostic_bound(
        "alanua/Skeleton",
        issue_number,
        _diagnostic_body(sha=sha, idempotency=idempotency),
        idempotency,
    )


def _private_envelope(bound: object, *, issue_number: int = 42, idempotency: str = "diag-key", observed: float = 90.0, expires: float = 120.0) -> dict[str, object]:
    snapshot = {
        "schema": poll.EXTERNAL_NODE_SNAPSHOT_SCHEMA,
        "node_id": poll.DIAGNOSTIC_NODE_ID,
        "generation": 7,
        "route_rank": poll.DIAGNOSTIC_ROUTE_RANK,
        "capabilities": list(bound.universal_task.required_capabilities),
        "supported_adapters": [bound.binding.adapter_id],
        "supported_lanes": [bound.binding.lane.value],
        "privacy_classes": [bound.universal_task.privacy.value],
        "resource_patterns": list(bound.universal_task.target_resources),
        "observed_at": observed,
        "expires_at": expires,
        "attestation_ref": "attestation:external-boundary:0123456789abcdef",
    }
    base = {
        "schema": poll.EXTERNAL_ATTESTATION_ENVELOPE_SCHEMA,
        "repository": "alanua/Skeleton",
        "target_issue": issue_number,
        "profile": poll.EXTERNAL_ATTESTATION_PROFILE,
        "route": bound.route,
        "binding_sha256": bound.source_binding_hash,
        "idempotency_sha256": hashlib.sha256(idempotency.encode("utf-8")).hexdigest(),
        "generation": 7,
        "snapshot": snapshot,
    }
    return {
        **base,
        "payload_sha256": hashlib.sha256(
            json.dumps(base, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def _install_private_envelope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, envelope: dict[str, object]) -> None:
    root = tmp_path / "vnext"
    root.mkdir(mode=0o700)
    path = root / poll.EXTERNAL_ATTESTATION_FILENAME
    path.write_text(json.dumps(envelope), encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv(poll.legacy.RUNNER_VNEXT_STATE_ROOT_ENV, str(root))
    monkeypatch.delenv(poll.legacy.RUNNER_VNEXT_NODE_SNAPSHOT_JSON_ENV, raising=False)


def test_validation_task_binds_exact_pr_head_base_and_files() -> None:
    head = "a" * 40
    base = "b" * 40
    body = f"""Repository: alanua/Skeleton
Pull Request: 77
Expected Head SHA: {head}
Expected Base SHA: {base}
Validation Profile: full_pytest
Allowed Files:
- core/a.py
- tests/test_a.py
Intent: exact validation
"""
    task, pr_number = poll._validation_task(_Item({"number": 1, "body": body}), body)

    assert pr_number == 77
    assert task.repo == "alanua/Skeleton"
    assert task.base_sha == head
    assert task.payload["expected_head_sha"] == head
    assert task.payload["expected_base_sha"] == base
    assert task.payload["profile"] == "full_pytest"
    assert task.allowed_files == ("core/a.py", "tests/test_a.py")
    assert task.requested_capabilities == ("repository_read", "test_execution")


def test_private_external_attestation_is_exact_and_fresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bound = _diagnostic_bound()
    envelope = _private_envelope(bound)
    _install_private_envelope(monkeypatch, tmp_path, envelope)

    snapshot, loaded = poll._attest(
        bound,
        expected_repository="alanua/Skeleton",
        expected_issue=42,
        expected_binding_sha256=bound.source_binding_hash,
        expected_idempotency_key="diag-key",
        now=100.0,
    )

    assert loaded == envelope
    assert snapshot.node_id == poll.DIAGNOSTIC_NODE_ID
    assert snapshot.supported_adapters == ("adapter:harmless-diagnostic",)
    assert snapshot.resource_patterns == ("repo:docs/RUNNER_MAINTENANCE_TASKS.md",)


def test_environment_snapshot_is_rejected_before_private_file(monkeypatch: pytest.MonkeyPatch) -> None:
    bound = _diagnostic_bound()
    monkeypatch.setenv(poll.legacy.RUNNER_VNEXT_NODE_SNAPSHOT_JSON_ENV, "{}")

    with pytest.raises(poll.VNextPollerError) as exc:
        poll._attest(
            bound,
            expected_repository="alanua/Skeleton",
            expected_issue=42,
            expected_binding_sha256=bound.source_binding_hash,
            expected_idempotency_key="diag-key",
            now=100.0,
        )

    assert exc.value.reason_code == "VNEXT_EXTERNAL_ATTESTATION_ENV_FORBIDDEN"


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (lambda payload: payload.update(repository="other/repo"), "VNEXT_EXTERNAL_ATTESTATION_IDENTITY_MISMATCH"),
        (lambda payload: payload.update(binding_sha256="b" * 64), "VNEXT_EXTERNAL_ATTESTATION_BINDING_MISMATCH"),
        (lambda payload: payload["snapshot"].update(expires_at=99.0), "VNEXT_EXTERNAL_ATTESTATION_DIGEST_MISMATCH"),
    ],
)
def test_private_attestation_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutator, reason: str) -> None:
    bound = _diagnostic_bound()
    envelope = _private_envelope(bound)
    mutator(envelope)
    _install_private_envelope(monkeypatch, tmp_path, envelope)

    with pytest.raises(poll.VNextPollerError) as exc:
        poll._attest(
            bound,
            expected_repository="alanua/Skeleton",
            expected_issue=42,
            expected_binding_sha256=bound.source_binding_hash,
            expected_idempotency_key="diag-key",
            now=100.0,
        )

    assert exc.value.reason_code == reason


def test_exact_canary_fails_before_store_open_when_attestation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _diagnostic_body()
    bound = _diagnostic_bound()
    monkeypatch.setattr(
        poll,
        "_fetch_exact_issue",
        lambda repository, issue_number: {"number": issue_number, "body": body, "state": "OPEN"},
    )
    monkeypatch.setattr(poll, "_current_repo_head_ref", lambda: bound.target_state_ref)
    monkeypatch.setattr(
        poll,
        "_attest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(poll.VNextPollerError("VNEXT_EXTERNAL_ATTESTATION_STALE")),
    )
    monkeypatch.setattr(
        poll,
        "_stores",
        lambda: (_ for _ in ()).throw(AssertionError("stores must not open before attestation")),
    )

    with pytest.raises(poll.VNextPollerError) as exc:
        poll.run_exact_green_canary(
            repository="alanua/Skeleton",
            target_issue=42,
            expected_binding_sha256=bound.source_binding_hash,
            expected_idempotency_key="diag-key",
        )

    assert exc.value.reason_code == "VNEXT_EXTERNAL_ATTESTATION_STALE"


def test_exact_canary_selector_never_enumerates_ready_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _diagnostic_body()
    bound = _diagnostic_bound()
    snapshot = poll.NodeCapabilitySnapshot(
        node_id=poll.DIAGNOSTIC_NODE_ID,
        generation=7,
        route_rank=poll.DIAGNOSTIC_ROUTE_RANK,
        capabilities=tuple(bound.universal_task.required_capabilities),
        supported_adapters=(bound.binding.adapter_id,),
        supported_lanes=(bound.binding.lane,),
        privacy_classes=(bound.universal_task.privacy,),
        resource_patterns=tuple(bound.universal_task.target_resources),
        observed_at=90.0,
        expires_at=120.0,
        attestation_ref="attestation:external-boundary:0123456789abcdef",
    )
    monkeypatch.setattr(
        poll,
        "_fetch_exact_issue",
        lambda repository, issue_number: {"number": issue_number, "body": body, "state": "OPEN"},
    )
    monkeypatch.setattr(poll, "_current_repo_head_ref", lambda: bound.target_state_ref)
    monkeypatch.setattr(poll, "_attest", lambda *_args, **_kwargs: (snapshot, {}))
    monkeypatch.setattr(poll, "_require_existing_persistent_stores", lambda: None)
    monkeypatch.setattr(
        poll.legacy,
        "get_ready_issue_items",
        lambda: (_ for _ in ()).throw(AssertionError("exact canary must never enumerate the queue")),
    )

    class _Stores:
        def close(self) -> None:
            pass

    class _Receipt:
        terminal_status = "COMPLETED"
        def to_public_mapping(self):
            return {"replayed": False, "execution_started": True, "lease_released": True}

    monkeypatch.setattr(poll, "_stores", lambda: _Stores())
    monkeypatch.setattr(poll, "run_green_authoritative_dispatch", lambda **kwargs: _Receipt())

    receipt = poll.run_exact_green_canary(
        repository="alanua/Skeleton",
        target_issue=42,
        expected_binding_sha256=bound.source_binding_hash,
        expected_idempotency_key="diag-key",
    )

    assert receipt["status"] == "DONE"
    assert receipt["profile"] == poll.EXTERNAL_ATTESTATION_PROFILE


def test_publication_task_binds_retained_worktree_head(monkeypatch: pytest.MonkeyPatch) -> None:
    head = "c" * 40
    monkeypatch.setattr(poll, "_worktree_head_ref", lambda issue: f"git:{head}")
    body = """Target Repository: alanua/Skeleton
Source Issue: 99
Output Branch: runner/issue-99
Allowed Files:
- core/a.py
- tests/test_a.py
Intent: exact publication
"""
    task, source_issue = poll._publication_task(_Item({"number": 2, "body": body}), body)

    assert source_issue == 99
    assert task.base_sha == head
    assert task.branch == "runner/issue-99"
    assert task.payload["source_issue"] == 99
    assert task.allowed_files == ("core/a.py", "tests/test_a.py")
    assert "publish_pull_request" in task.requested_capabilities


def test_dispatch_has_no_untyped_legacy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _Item({"number": 3, "body": ""})
    monkeypatch.setattr(poll.legacy, "extract_runtime_maintenance_task_id", lambda body: (False, None))
    monkeypatch.setattr(
        poll.legacy,
        "extract_telegram_approved_pr_merge_request",
        lambda body: (False, None, None),
    )
    monkeypatch.setattr(
        poll.legacy,
        "extract_runner_task",
        lambda body, default_repository: (None, None),
    )

    with pytest.raises(poll.VNextPollerError) as exc:
        poll.dispatch_item(item)

    assert exc.value.reason_code == "VNEXT_TYPED_TASK_REQUIRED"


def test_dispatch_routes_validation_to_vnext_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _Item({"number": 4, "body": "validation"})
    called: list[int] = []
    monkeypatch.setattr(
        poll.legacy,
        "extract_runtime_maintenance_task_id",
        lambda body: (True, poll.legacy.VALIDATE_PR_BRANCH),
    )
    monkeypatch.setattr(
        poll.legacy,
        "extract_telegram_approved_pr_merge_request",
        lambda body: (False, None, None),
    )
    monkeypatch.setattr(
        poll.legacy,
        "extract_runner_task",
        lambda body, default_repository: (None, None),
    )
    monkeypatch.setattr(
        poll,
        "_dispatch_validation",
        lambda routed_item, body, workdir: called.append(routed_item.issue_number),
    )
    monkeypatch.setattr(
        poll.legacy,
        "process_issue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy fallback forbidden")),
    )

    poll.dispatch_item(item)

    assert called == [4]


def test_unsigned_merge_is_rejected_before_legacy_mechanics(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Merge:
        pr_number = 10
        approved_head_sha = "d" * 40
        callback_digest = "0123456789ab"

    item = _Item({"number": 5, "body": "merge"})
    monkeypatch.setattr(poll.legacy, "telegram_approve_digest_is_signed", lambda request: False)
    monkeypatch.setattr(
        poll.legacy,
        "process_issue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy mechanics forbidden")),
    )

    with pytest.raises(poll.VNextPollerError) as exc:
        poll._dispatch_merge(item, "merge", _Merge(), None)

    assert exc.value.reason_code == "VNEXT_MERGE_SIGNED_APPROVAL_REQUIRED"


def test_merge_requires_matching_operator_audit_before_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Merge:
        pr_number = 10
        approved_head_sha = "d" * 40
        callback_digest = "0123456789ab"

    item = _Item({"number": 6, "body": "merge"})
    monkeypatch.setattr(poll.legacy, "telegram_approve_digest_is_signed", lambda request: True)
    monkeypatch.setattr(poll.legacy, "get_pr_merge_state", lambda pr_number: {"number": pr_number})
    monkeypatch.setattr(poll.legacy, "_pr_merge_block_reason", lambda request, state: "audit mismatch")
    monkeypatch.setattr(
        poll.legacy,
        "process_issue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy mechanics forbidden")),
    )

    with pytest.raises(poll.VNextPollerError) as exc:
        poll._dispatch_merge(item, "merge", _Merge(), None)

    assert exc.value.reason_code == "VNEXT_MERGE_APPROVAL_AUDIT_INVALID"
