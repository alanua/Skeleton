from __future__ import annotations

import json
from pathlib import Path

from core.resolver_registry import ResolverCapabilityManifest, ResolverCapabilityRegistry


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "fixtures/resolver_capabilities/anitube_origin_protected/manifest.json"


def test_registry_separates_canonical_identity_from_live_state(tmp_path: Path) -> None:
    manifest = ResolverCapabilityManifest.from_mapping(json.loads(MANIFEST.read_text(encoding="utf-8")))
    registry = ResolverCapabilityRegistry(
        canon_path=tmp_path / "device-registry-compatible.json",
        state_path=tmp_path / "state-database-compatible.json",
    )

    canonical = registry.register_canonical(
        manifest,
        package_hash="a" * 64,
        manifest_hash="b" * 64,
    )
    state = registry.append_receipt(
        capability_id=manifest.capability_id,
        node_id="home-edge-01",
        status="canary",
        receipt={"stage": "canary"},
        active_version=manifest.version,
        package_hash="a" * 64,
    )

    assert canonical.to_mapping()["schema"] == "skeleton.resolver_capability.canon.v1"
    assert state.to_mapping()["schema"] == "skeleton.resolver_capability.state.v1"
    assert registry.canonical(manifest.capability_id, manifest.version)["package_hash"] == "a" * 64
    assert registry.state(manifest.capability_id, "home-edge-01").status == "canary"


def test_compatible_node_discovers_immutable_package_by_identity_and_hash(tmp_path: Path) -> None:
    manifest = ResolverCapabilityManifest.from_mapping(json.loads(MANIFEST.read_text(encoding="utf-8")))
    registry = ResolverCapabilityRegistry(canon_path=tmp_path / "canon.json", state_path=tmp_path / "state.json")
    registry.register_canonical(manifest, package_hash="c" * 64, manifest_hash="d" * 64)

    matches = registry.discover_compatible(
        host="anitube.in.ua",
        runtime_version="skeleton-cast-1",
        package_hash="c" * 64,
    )

    assert [match["capability_id"] for match in matches] == [manifest.capability_id]
    assert matches[0]["version"] == "1.0.0"
