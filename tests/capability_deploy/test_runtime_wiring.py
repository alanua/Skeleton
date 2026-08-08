from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.resolver_registry import ResolverCapabilityManifest
from core.resolver_registry.models import ResolverCapabilityError
from ops.capability_deploy import reject_dead_resolver_module


ROOT = Path(__file__).resolve().parents[2]


def test_isolated_unused_resolver_module_is_rejected() -> None:
    with pytest.raises(ResolverCapabilityError, match="no runtime import path"):
        reject_dead_resolver_module(
            runtime_sources={"resolver.py": "def resolve_page(url): return {}"},
            capability_module="isolated_unused_resolver.py",
            deploy_operation={"operation_id": "deploy"},
        )


def test_skeleton_cast_anitube_capability_is_wired_into_runtime_path() -> None:
    resolver = (ROOT / "ops/skeleton_cast/runtime/resolver.py").read_text(encoding="utf-8")
    adapter = (ROOT / "ops/skeleton_cast/runtime/capability_adapter.py").read_text(encoding="utf-8")
    manifest = ResolverCapabilityManifest.from_mapping(
        json.loads((ROOT / "fixtures/resolver_capabilities/anitube_origin_protected/manifest.json").read_text(encoding="utf-8"))
    )

    reject_dead_resolver_module(
        runtime_sources={"resolver.py": resolver, "capability_adapter.py": adapter},
        capability_module="active_resolver_capabilities",
        deploy_operation=manifest.deploy,
    )
    assert "ACTIVE_RESOLVER_CAPABILITIES = active_resolver_capabilities()" in resolver
    assert manifest.capability_id in adapter
