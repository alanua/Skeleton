from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from core.capabilities import CapabilityPackage, RolloutController, VerificationState
from core.resolver_registry import ResolverCapabilityManifest, ResolverCapabilityRegistry
from core.resolver_registry.models import ResolverCapabilityError


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "fixtures/resolver_capabilities/anitube_origin_protected/manifest.json"


def test_hash_pinned_capability_package_verifies(tmp_path: Path) -> None:
    artifact = _make_artifact(tmp_path)
    package = CapabilityPackage(artifact)
    verification = package.verify(expected_hash=package.sha256())

    assert verification.ok
    assert verification.package_hash == package.sha256()
    assert verification.manifest_hash is not None


def test_canary_activation_requires_registered_operation_and_blocks_unapproved_production(tmp_path: Path) -> None:
    manifest = ResolverCapabilityManifest.from_mapping(json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))
    registry = ResolverCapabilityRegistry(canon_path=tmp_path / "canon.json", state_path=tmp_path / "state.json")
    rollout = RolloutController(registry=registry)

    canary = rollout.canary(manifest, package_hash="e" * 64, approval_ref="operator-approved-canary")
    assert canary.status == "canary"
    assert canary.deployment_receipts[-1]["request"]["argv"] == ["ops/skeleton_cast/deploy.sh"]

    with pytest.raises(ResolverCapabilityError, match="production activation requires Skeleton approval"):
        rollout.activate(
            manifest,
            package_hash="e" * 64,
            approval_ref=None,
            verification=VerificationState(True, True, True, True, True),
        )

    with pytest.raises(ResolverCapabilityError, match="independent successful verification"):
        rollout.activate(
            manifest,
            package_hash="e" * 64,
            approval_ref="operator-production",
            verification=VerificationState(True, True, True, False, True),
        )


def test_activation_and_rollback_persist_receipts_and_restore_prior_version(tmp_path: Path) -> None:
    manifest = ResolverCapabilityManifest.from_mapping(json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))
    registry = ResolverCapabilityRegistry(canon_path=tmp_path / "canon.json", state_path=tmp_path / "state.json")
    registry.record_state(
        registry.append_receipt(
            capability_id=manifest.capability_id,
            node_id="home-edge-01",
            status="active",
            receipt={"stage": "seed"},
            active_version="0.9.0",
            package_hash="0" * 64,
        )
    )
    rollout = RolloutController(registry=registry)

    rollout.canary(manifest, package_hash="f" * 64, approval_ref="operator-canary")
    active = rollout.activate(
        manifest,
        package_hash="f" * 64,
        approval_ref="operator-production",
        verification=VerificationState(True, True, True, True, True),
    )
    rolled_back = rollout.rollback(manifest, approval_ref="operator-rollback", health_verified=True)

    assert active.status == "active"
    assert rolled_back.status == "rolled_back"
    assert rolled_back.active_version == "0.9.0"
    assert rolled_back.deployment_receipts[-1]["request"]["argv"] == ["ops/skeleton_cast/rollback.sh"]


def test_compatibility_checks_runtime_node_os_and_tools(tmp_path: Path) -> None:
    manifest = ResolverCapabilityManifest.from_mapping(json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))
    rollout = RolloutController(
        registry=ResolverCapabilityRegistry(canon_path=tmp_path / "canon.json", state_path=tmp_path / "state.json")
    )

    result = rollout.compatibility_check(
        manifest,
        runtime_version="cast-1",
        execution_node="home-edge-01",
        os_name="linux",
        tools={"curl": "/usr/bin/curl"},
    )

    assert result["compatible"] is True


def _make_artifact(tmp_path: Path) -> Path:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    artifact = tmp_path / "anitube-origin-protected-1.0.0.tar.gz"
    files = {
        "manifest.json": json.dumps(manifest, sort_keys=True).encode(),
        "dependency-lock.json": b'{"schema":"lock"}',
        "fixtures/origin_protected.json": b'{"status":"origin_protected"}',
        "tests/test_origin_protected.py": b"def test_fixture(): assert True\n",
        "code/resolver.py": b"CAPABILITY_ID = 'skeleton_cast.anitube.origin_protected'\n",
        "operations/deploy.json": b'{"operation_id":"skeleton_cast.resolver.deploy"}',
        "operations/verify.json": b'{"operation_id":"skeleton_cast.resolver.verify"}',
        "operations/rollback.json": b'{"operation_id":"skeleton_cast.resolver.rollback"}',
        "attestation.json": json.dumps(
            {
                "schema": "skeleton.resolver_capability.attestation.v1",
                "manifest_sha256": manifest_hash,
                "package_sha256": "external",
                "signature": "test-attestation-signature",
            },
            sort_keys=True,
        ).encode(),
    }
    with tarfile.open(artifact, "w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return artifact
