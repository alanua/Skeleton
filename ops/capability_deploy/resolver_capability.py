from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from core.capabilities.package import CapabilityPackage
from core.capabilities.rollout import RolloutController
from core.resolver_registry.models import ResolverCapabilityError


def build_install_plan(
    artifact_path: Path,
    *,
    rollout: RolloutController,
    runtime_version: str,
    execution_node: str,
) -> dict[str, Any]:
    package = CapabilityPackage(artifact_path)
    verification = package.verify()
    if not verification.ok:
        raise ResolverCapabilityError("; ".join(verification.errors))
    manifest = package.manifest()
    compatibility = rollout.compatibility_check(
        manifest,
        runtime_version=runtime_version,
        execution_node=execution_node,
    )
    if not compatibility["compatible"]:
        raise ResolverCapabilityError("capability package is not compatible with this node")
    return {
        "schema": "skeleton.resolver_capability.install_plan.v1",
        "capability_id": manifest.capability_id,
        "version": manifest.version,
        "package_hash": verification.package_hash,
        "compatibility": compatibility,
        "dry_run": rollout.dry_run(manifest, package_hash=verification.package_hash),
    }


def reject_dead_resolver_module(
    *,
    runtime_sources: Mapping[str, str],
    capability_module: str,
    deploy_operation: Mapping[str, Any] | None,
) -> None:
    joined = "\n".join(runtime_sources.values())
    if capability_module not in joined:
        raise ResolverCapabilityError("resolver capability module has no runtime import path")
    if not deploy_operation or not deploy_operation.get("operation_id"):
        raise ResolverCapabilityError("resolver capability has no registered deploy operation")
