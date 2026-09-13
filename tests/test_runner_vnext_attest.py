from __future__ import annotations

import pytest

import scripts.runner_vnext_attest as attest


def test_recovery_adapter_is_explicitly_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        attest,
        "_probe_capabilities",
        lambda lane, capabilities: (True, {"synthetic": True}, "a" * 40),
    )
    monkeypatch.setattr(attest.time, "time", lambda: 100.0)
    monkeypatch.setattr(attest.time, "time_ns", lambda: 123456789)

    snapshot = attest.build_snapshot(
        lane="control",
        adapter="adapter:operation-recovery",
        capabilities=("repository_maintenance", "diagnostic_read", "subprocess_isolated"),
        resources=("control:issue-1", "repo:alanua/Skeleton"),
        privacy="PUBLIC_SAFE",
        ttl_seconds=45.0,
    )

    assert snapshot["supported_adapters"] == ["adapter:operation-recovery"]
    assert snapshot["supported_lanes"] == ["control"]
    assert snapshot["expires_at"] == 145.0
    assert str(snapshot["attestation_ref"]).startswith("attestation:runtime-probe:")


def test_attestor_rejects_wrong_adapter_without_probing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        attest,
        "_probe_capabilities",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("probe must not run")),
    )
    with pytest.raises(ValueError, match="attestor_adapter_invalid"):
        attest.build_snapshot(
            lane="control",
            adapter="adapter:exact-operator-merge",
            capabilities=("repository_maintenance",),
            resources=("control:issue-1",),
            privacy="PUBLIC_SAFE",
            ttl_seconds=45.0,
        )


def test_attestor_rejects_widened_resource_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        attest,
        "_probe_capabilities",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("probe must not run")),
    )
    with pytest.raises(ValueError, match="attestor_resource_not_allowed"):
        attest.build_snapshot(
            lane="validate",
            adapter="adapter:repo-validation",
            capabilities=("repository_read", "test_execution"),
            resources=("control:anything",),
            privacy="PUBLIC_SAFE",
            ttl_seconds=45.0,
        )


def test_attestor_rejects_overlong_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        attest,
        "_probe_capabilities",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("probe must not run")),
    )
    with pytest.raises(ValueError, match="attestor_ttl_invalid"):
        attest.build_snapshot(
            lane="publish",
            adapter="adapter:draft-publication",
            capabilities=("repository_read", "publish_pull_request"),
            resources=("repo:file.py",),
            privacy="PUBLIC_SAFE",
            ttl_seconds=61.0,
        )
