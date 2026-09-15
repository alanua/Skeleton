from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import dataclass
from typing import Any, Mapping

from core.home_edge.executor import ExecutionLane, ExecutionUser, HomeEdgeExecRequest
from core.resolver_registry.models import CapabilityStateRecord, ResolverCapabilityManifest, ResolverCapabilityError
from core.resolver_registry.registry import ResolverCapabilityRegistry


VERIFICATION_STATES = ("sent", "accepted", "applied", "physically_verified", "application_verified")


@dataclass(frozen=True)
class VerificationState:
    sent: bool = False
    accepted: bool = False
    applied: bool = False
    physically_verified: bool = False
    application_verified: bool = False

    @property
    def successful(self) -> bool:
        return self.sent and self.accepted and self.applied and self.physically_verified and self.application_verified

    def to_mapping(self) -> dict[str, bool]:
        return {name: bool(getattr(self, name)) for name in VERIFICATION_STATES}


class RolloutController:
    """Staged resolver capability rollout using registered Skeleton operations."""

    def __init__(self, *, registry: ResolverCapabilityRegistry, node_id: str = "home-edge-01") -> None:
        self.registry = registry
        self.node_id = node_id

    def compatibility_check(
        self,
        manifest: ResolverCapabilityManifest,
        *,
        runtime_version: str,
        execution_node: str,
        os_name: str | None = None,
        tools: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        required_tools = [str(tool) for tool in manifest.dependencies.get("tools", ())]
        available = dict(tools or {tool: shutil.which(tool) or "" for tool in required_tools})
        missing = [tool for tool in required_tools if not available.get(tool)]
        python_req = str(manifest.dependencies.get("python") or "")
        python_ok = python_req in {"", ">=3.11"} or sys.version.split()[0].startswith(python_req.strip("="))
        os_ok = (os_name or platform.system().lower()).lower() in {"linux", "darwin"} if os_name else True
        return {
            "schema": "skeleton.resolver_capability.compatibility.v1",
            "runtime_version": runtime_version,
            "execution_node": execution_node,
            "python_ok": python_ok,
            "os_ok": os_ok,
            "missing_tools": missing,
            "compatible": python_ok and os_ok and execution_node == self.node_id and not missing,
        }

    def dry_run(self, manifest: ResolverCapabilityManifest, *, package_hash: str) -> dict[str, Any]:
        return {
            "schema": "skeleton.resolver_capability.dry_run.v1",
            "capability_id": manifest.capability_id,
            "version": manifest.version,
            "package_hash": package_hash,
            "operations": {
                "deploy": dict(manifest.deploy),
                "verify": dict(manifest.verify),
                "rollback": dict(manifest.rollback),
            },
            "would_use_home_edge": True,
        }

    def canary(self, manifest: ResolverCapabilityManifest, *, package_hash: str, approval_ref: str) -> CapabilityStateRecord:
        self._require_merge_approval(manifest)
        request = self._operation_request(manifest.deploy, approval_ref=approval_ref)
        return self.registry.append_receipt(
            capability_id=manifest.capability_id,
            node_id=self.node_id,
            status="canary",
            active_version=manifest.version,
            package_hash=package_hash,
            rollback_version=self._current_version(manifest.capability_id),
            receipt={"stage": "canary", "request": request.to_mapping(include_signature=False)},
        )

    def activate(
        self,
        manifest: ResolverCapabilityManifest,
        *,
        package_hash: str,
        approval_ref: str | None,
        verification: VerificationState,
    ) -> CapabilityStateRecord:
        if not approval_ref:
            raise ResolverCapabilityError("production activation requires Skeleton approval")
        if not verification.successful:
            raise ResolverCapabilityError("production activation requires independent successful verification")
        request = self._operation_request(manifest.verify, approval_ref=approval_ref)
        return self.registry.append_receipt(
            capability_id=manifest.capability_id,
            node_id=self.node_id,
            status="active",
            active_version=manifest.version,
            package_hash=package_hash,
            receipt={"stage": "activation", "verification": verification.to_mapping(), "request": request.to_mapping(include_signature=False)},
        )

    def rollback(self, manifest: ResolverCapabilityManifest, *, approval_ref: str, health_verified: bool) -> CapabilityStateRecord:
        if not health_verified:
            raise ResolverCapabilityError("rollback requires runtime health verification")
        current = self.registry.state(manifest.capability_id, self.node_id)
        rollback_version = current.rollback_version if current else None
        request = self._operation_request(manifest.rollback, approval_ref=approval_ref)
        return self.registry.append_receipt(
            capability_id=manifest.capability_id,
            node_id=self.node_id,
            status="rolled_back",
            active_version=rollback_version,
            receipt={"stage": "rollback", "health_verified": True, "request": request.to_mapping(include_signature=False)},
        )

    def _operation_request(self, operation: Mapping[str, Any], *, approval_ref: str) -> HomeEdgeExecRequest:
        return HomeEdgeExecRequest.from_mapping(
            {
                "node_id": self.node_id,
                "execution_lane": ExecutionLane.ROUTINE_MUTATION.value,
                "operator_approval_ref": approval_ref,
                "run_as": ExecutionUser.DESKTOP_USER.value,
                "argv": list(operation["args"]),
                "timeout_seconds": 300,
                "idempotency_key": str(operation["operation_id"]),
            }
        )

    @staticmethod
    def _require_merge_approval(manifest: ResolverCapabilityManifest) -> None:
        if not manifest.approvals.get("merge"):
            raise ResolverCapabilityError("canary requires approved capability record")

    def _current_version(self, capability_id: str) -> str | None:
        current = self.registry.state(capability_id, self.node_id)
        return current.active_version if current else None
