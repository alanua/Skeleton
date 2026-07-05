from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from core.canonical_memory import (
    CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
    CANONICAL_OPERATOR_PREFERENCES_SCOPE,
    FAST_AUTONOMOUS_EXECUTION_KEY,
)
from core.canonical_memory_import import (
    CanonicalMemoryImportError,
    import_approved_operator_preference_manifest,
)
from core.canonical_memory_manifest import prepare_canonical_memory_manifest
from core.graphify_adapter import GraphifyAdapterError
from core.mempalace_adapter import MemPalaceAdapter, MemPalaceAdapterError
from core.mempalace_projection import MEMPALACE_SYNTHETIC_NAMESPACE, MEMPALACE_SYNTHETIC_PROJECT_ID
from core.memory_gateway_policy import (
    ALLOWED_COMMAND_SUFFIXES,
    ALLOWED_NAMESPACES,
    EXACT_CONFIRMATION_REVISION_MISMATCH,
    GRAPH_RESULT_NOT_CANON_CONFIRMED,
    PUBLIC_MODE_FORBIDDEN_NAMESPACES,
    SEMANTIC_RESULT_NOT_CANON_CONFIRMED,
    STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE,
    MemoryGatewayPolicyError,
    command_name,
    sanitized_actor_ref,
    sanitized_reason_code,
    split_command,
    validate_namespace,
    build_command_receipt,
    validate_public_payload,
)
from core.memory_override import MemoryOverrideRegistry
from core.memory_patch_proposal import MemoryPatchProposalRegistry
from core.skeleton_memory import SkeletonMemory


MEMORY_GATEWAY_REQUEST_SCHEMA = "skeleton.memory_gateway.request.v1"
MEMORY_GATEWAY_RESPONSE_SCHEMA = "skeleton.memory_gateway.response.v1"
MEMORY_GATEWAY_AUDIT_SCHEMA = "skeleton.memory_gateway.audit_event.v1"
MEMORY_GATEWAY_CONTRACT_VERSION = "1.0.0"
GRAPHIFY_SYNTHETIC_NAMESPACE = "skeleton"
GRAPHIFY_SYNTHETIC_PROJECT_ID = "graphify_synthetic"
_SAFE_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_PRIVATE_EXACT_AUTHORIZATION = object()


@dataclass(frozen=True)
class GatewayCapabilityToken:
    schema: str
    namespaces: tuple[str, ...]
    public_mode: bool = True


def capability_token(*, namespaces: tuple[str, ...], public_mode: bool = True) -> GatewayCapabilityToken:
    return GatewayCapabilityToken(
        schema="skeleton.memory_gateway.capability_token.v1",
        namespaces=namespaces,
        public_mode=public_mode,
    )


class MemoryGateway:
    """Bounded synthetic Memory Gateway contract with namespace-isolated policy."""

    def __init__(
        self,
        token: GatewayCapabilityToken | Mapping[str, Any],
        *,
        patch_registry: MemoryPatchProposalRegistry | None = None,
        override_registry: MemoryOverrideRegistry | None = None,
        mempalace_adapter: MemPalaceAdapter | None = None,
        graphify_adapter: object | None = None,
        skeleton_memory: SkeletonMemory | None = None,
    ) -> None:
        self._token = _normalize_token(token)
        self._patch_registry = patch_registry or MemoryPatchProposalRegistry()
        self._override_registry = override_registry or MemoryOverrideRegistry()
        self._audit_log: list[dict[str, object]] = []
        self._canonical = _canonical_seed()
        self._semantic = _semantic_seed()
        self._graph = _graph_seed()
        self._freshness = _freshness_seed()
        self._mempalace_adapter = mempalace_adapter
        self._graphify_adapter = graphify_adapter
        self._skeleton_memory = skeleton_memory

    def execute(self, request: Mapping[str, Any]) -> dict[str, object]:
        if not isinstance(request, Mapping):
            raise MemoryGatewayPolicyError("INVALID_REQUEST", "request must be an object")
        if request.get("schema") != MEMORY_GATEWAY_REQUEST_SCHEMA:
            raise MemoryGatewayPolicyError("INVALID_REQUEST_SCHEMA", "request schema is invalid")
        command = request.get("command")
        namespace_from_command, suffix = split_command(str(command))
        namespace = self._authorize_namespace(request.get("namespace"))
        if namespace_from_command != namespace:
            raise MemoryGatewayPolicyError("COMMAND_NAMESPACE_MISMATCH", "command namespace mismatch")
        payload = request.get("payload", {})
        if not isinstance(payload, Mapping):
            raise MemoryGatewayPolicyError("INVALID_REQUEST", "payload must be an object")

        handlers = {
            "memory.lookup_exact": self.lookup_exact,
            "memory.search_semantic": self.search_semantic,
            "memory.get_conflicts": self.get_conflicts,
            "memory.get_override_history": self.get_override_history,
            "memory.get_audit_log": self.get_audit_log,
            "memory.get_index_freshness": self.get_memory_index_freshness,
            "memory.prepare_canonical_manifest": self.prepare_canonical_manifest,
            "memory.import_canonical_manifest": self.import_canonical_manifest,
            "graph.query_code": self.query_code,
            "graph.get_index_freshness": self.get_graph_index_freshness,
            "memory.propose_patch": self.propose_patch,
        }
        if suffix == "memory.import_canonical_manifest":
            return self.import_canonical_manifest(
                namespace=namespace,
                manifest=payload.get("manifest"),
                project_id=payload.get("project_id"),
            )
        return handlers[suffix](namespace=namespace, **payload)

    def lookup_exact(
        self,
        *,
        namespace: str,
        key: str,
        project_id: object = None,
        _authorization: object = None,
    ) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        project_id = self._scope_project_id(namespace, project_id)
        key = _safe_exact_key(key)
        try:
            record = self._canonical[(namespace, project_id)][key]
        except KeyError as exc:
            record = self._lookup_sqlite_canonical_exact(
                namespace=namespace,
                project_id=project_id,
                key=key,
            )
            if record is None:
                raise MemoryGatewayPolicyError(
                    "CANONICAL_FACT_NOT_FOUND",
                    "canonical fact not found",
                ) from exc

        payload = {
            **deepcopy(record),
            "namespace": namespace,
            "project_id": project_id,
            "authoritative": True,
            "authority_classification": "canonical_exact",
            "source_kind": "canonical_sqlite",
        }

        if _authorization is _PRIVATE_EXACT_AUTHORIZATION:
            response = {
                "schema": MEMORY_GATEWAY_RESPONSE_SCHEMA,
                "contract_version": MEMORY_GATEWAY_CONTRACT_VERSION,
                "namespace": namespace,
                "command": command_name(namespace, "memory.lookup_exact"),
                "payload": payload,
            }
            json.dumps(response, allow_nan=False, sort_keys=True)
            return response

        return self._response(
            namespace=namespace,
            command_suffix="memory.lookup_exact",
            payload=payload,
        )

    def search_semantic(self, *, namespace: str, query: str, project_id: object = None) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        project_id = self._scope_project_id(namespace, project_id)
        _safe_lookup_key(query)
        if _is_mempalace_synthetic_scope(namespace, project_id):
            if self._mempalace_adapter is None:
                raise MemoryGatewayPolicyError(
                    "MEMPALACE_ADAPTER_REQUIRED",
                    "synthetic MemPalace route requires explicit adapter injection",
                )
            try:
                pilot = self._mempalace_adapter.search_semantic(
                    namespace=namespace,
                    project_id=project_id,
                    query=query,
                )
            except MemPalaceAdapterError as exc:
                raise MemoryGatewayPolicyError(exc.reason_code, str(exc)) from exc
            return self._response(
                namespace=namespace,
                command_suffix="memory.search_semantic",
                payload={"results": pilot["results"], "authoritative": False},
            )
        results = []
        for result in self._semantic[(namespace, project_id)]:
            rendered = deepcopy(result)
            rendered.update(
                {
                    "namespace": namespace,
                    "project_id": project_id,
                    "authoritative": False,
                    "authority_classification": "derived_semantic",
                    "source_kind": "mempalace",
                    "freshness": deepcopy(self._freshness[(namespace, project_id)]["mempalace"]),
                }
            )
            results.append(rendered)
        return self._response(
            namespace=namespace,
            command_suffix="memory.search_semantic",
            payload={"results": results},
        )

    def query_code(
        self,
        *,
        namespace: str,
        query: str,
        project_id: object = None,
        limit: int = 5,
    ) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        project_id = self._scope_project_id(namespace, project_id)
        _safe_lookup_key(query)
        if _is_graphify_synthetic_scope(namespace, project_id):
            if self._graphify_adapter is None:
                raise MemoryGatewayPolicyError(
                    "GRAPHIFY_ADAPTER_REQUIRED",
                    "synthetic Graphify route requires explicit adapter injection",
                )
            try:
                adapter_response = self._graphify_adapter.query_code(
                    namespace=namespace,
                    project_id=project_id,
                    query=query,
                    limit=limit,
                )
            except GraphifyAdapterError as exc:
                raise MemoryGatewayPolicyError(exc.reason_code, str(exc)) from exc
            if _is_gateway_response(adapter_response):
                return adapter_response
            return self._response(
                namespace=namespace,
                command_suffix="graph.query_code",
                payload=adapter_response,
            )
        _reject_wrong_graphify_synthetic_scope(namespace, project_id)
        if (namespace, project_id) not in self._graph:
            raise MemoryGatewayPolicyError("PROJECT_NOT_AUTHORIZED", "graph project scope is not authorized")
        results = []
        for result in self._graph[(namespace, project_id)]:
            rendered = deepcopy(result)
            rendered.update(
                {
                    "namespace": namespace,
                    "project_id": project_id,
                    "authoritative": False,
                    "authoritative_scope": "code_graph",
                    "authority_classification": "derived_code_graph",
                    "source_kind": "graphify",
                    "freshness": deepcopy(self._freshness[(namespace, project_id)]["graphify"]),
                }
            )
            results.append(rendered)
        return self._response(namespace=namespace, command_suffix="graph.query_code", payload={"results": results})

    def get_memory_index_freshness(self, *, namespace: str, project_id: object = None) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        project_id = self._scope_project_id(namespace, project_id)
        if _is_mempalace_synthetic_scope(namespace, project_id):
            if self._mempalace_adapter is None:
                raise MemoryGatewayPolicyError(
                    "MEMPALACE_ADAPTER_REQUIRED",
                    "synthetic MemPalace route requires explicit adapter injection",
                )
            try:
                freshness = self._mempalace_adapter.get_index_freshness(
                    namespace=namespace,
                    project_id=project_id,
                )
            except MemPalaceAdapterError as exc:
                raise MemoryGatewayPolicyError(exc.reason_code, str(exc)) from exc
            return self._response(
                namespace=namespace,
                command_suffix="memory.get_index_freshness",
                payload={
                    "project_id": project_id,
                    "mempalace": freshness,
                    "canonical_sqlite": {
                        "current_canonical_revision": freshness["current_canonical_revision"],
                        "project_id": project_id,
                    },
                    "authoritative": False,
                },
            )
        return self._response(
            namespace=namespace,
            command_suffix="memory.get_index_freshness",
            payload={
                "project_id": project_id,
                "mempalace": deepcopy(self._freshness[(namespace, project_id)]["mempalace"]),
                "canonical_sqlite": deepcopy(self._freshness[(namespace, project_id)]["canonical_sqlite"]),
            },
        )

    def get_graph_index_freshness(self, *, namespace: str, project_id: object = None) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        project_id = self._scope_project_id(namespace, project_id)
        if _is_graphify_synthetic_scope(namespace, project_id):
            if self._graphify_adapter is None:
                raise MemoryGatewayPolicyError(
                    "GRAPHIFY_ADAPTER_REQUIRED",
                    "synthetic Graphify route requires explicit adapter injection",
                )
            try:
                adapter_response = self._graphify_adapter.get_index_freshness(
                    namespace=namespace,
                    project_id=project_id,
                )
            except GraphifyAdapterError as exc:
                raise MemoryGatewayPolicyError(exc.reason_code, str(exc)) from exc
            if _is_gateway_response(adapter_response):
                return adapter_response
            return self._response(
                namespace=namespace,
                command_suffix="graph.get_index_freshness",
                payload={
                    "project_id": project_id,
                    "graphify": adapter_response,
                },
            )
        _reject_wrong_graphify_synthetic_scope(namespace, project_id)
        if (namespace, project_id) not in self._freshness:
            raise MemoryGatewayPolicyError("PROJECT_NOT_AUTHORIZED", "graph project scope is not authorized")
        return self._response(
            namespace=namespace,
            command_suffix="graph.get_index_freshness",
            payload={
                "project_id": project_id,
                "graphify": deepcopy(self._freshness[(namespace, project_id)]["graphify"]),
            },
        )

    def get_conflicts(self, *, namespace: str, project_id: object = None) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        project_id = self._scope_project_id(namespace, project_id)
        conflicts = [
            _sanitized_conflict(conflict)
            for conflict in self._patch_registry.get_conflicts()
            if _record_belongs_to_scope(conflict, namespace, project_id)
        ]
        return self._response(
            namespace=namespace,
            command_suffix="memory.get_conflicts",
            payload={
                "event_class": "source_value_conflict",
                "conflicts": conflicts,
            },
        )

    def get_override_history(
        self, *, namespace: str, override_ref: str, project_id: object = None
    ) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        project_id = self._scope_project_id(namespace, project_id)
        history = [
            event
            for event in self._override_registry.get_override_history(override_ref)
            if event.get("namespace") == namespace and event.get("project_id") == project_id
        ]
        return self._response(
            namespace=namespace,
            command_suffix="memory.get_override_history",
            payload={"event_class": "override_lifecycle", "events": history},
        )

    def get_audit_log(self, *, namespace: str, project_id: object = None) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        project_id = self._scope_project_id(namespace, project_id)
        return self._response(
            namespace=namespace,
            command_suffix="memory.get_audit_log",
            payload={
                "events": [
                    deepcopy(event)
                    for event in self._audit_log
                    if event.get("namespace") == namespace and event.get("project_id") == project_id
                ]
            },
        )

    def prepare_canonical_manifest(
        self, *, namespace: str, manifest: Mapping[str, Any], project_id: object = None
    ) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        if namespace != "skeleton":
            raise MemoryGatewayPolicyError(
                "CANONICAL_MANIFEST_NAMESPACE_NOT_AUTHORIZED",
                "canonical manifest preparation is only available through skeleton namespace",
            )
        project_id = self._scope_project_id(namespace, project_id)
        if project_id != "skeleton":
            raise MemoryGatewayPolicyError(
                "PROJECT_NOT_AUTHORIZED",
                "canonical manifest preparation is not project-scoped",
            )
        if not isinstance(manifest, Mapping):
            raise MemoryGatewayPolicyError("INVALID_CANONICAL_MANIFEST", "manifest must be an object")
        prepared = prepare_canonical_memory_manifest(manifest)
        if prepared["status"] == "REJECTED":
            raise MemoryGatewayPolicyError("INVALID_CANONICAL_MANIFEST", "canonical manifest failed validation")
        return self._response(
            namespace=namespace,
            command_suffix="memory.prepare_canonical_manifest",
            payload={
                "project_id": project_id,
                "preparation_status": prepared["status"],
                "authority": prepared["authority"],
                "authoritative": prepared["authoritative"],
                "source_kind": "canonical_memory_manifest",
                "integrity_hash": prepared["integrity_hash"],
                "integrity_check": prepared["integrity_check"],
                "manifest": prepared["manifest"],
            },
        )

    def import_canonical_manifest(
        self, *, namespace: str, manifest: Mapping[str, Any], project_id: object = None, **_untrusted_inputs: object
    ) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        if namespace != "skeleton":
            raise MemoryGatewayPolicyError(
                "CANONICAL_IMPORT_NAMESPACE_NOT_AUTHORIZED",
                "canonical import is only available through skeleton namespace",
            )
        project_id = self._scope_project_id(namespace, project_id)
        if project_id != "skeleton":
            raise MemoryGatewayPolicyError(
                "PROJECT_NOT_AUTHORIZED",
                "canonical import is not project-scoped",
            )
        if self._skeleton_memory is None:
            raise MemoryGatewayPolicyError(
                "TRUSTED_STORE_REQUIRED",
                "canonical import requires an injected SkeletonMemory store",
            )
        if not isinstance(manifest, Mapping):
            raise MemoryGatewayPolicyError("INVALID_CANONICAL_MANIFEST", "manifest must be an object")
        try:
            receipt = import_approved_operator_preference_manifest(
                store=self._skeleton_memory,
                manifest=manifest,
                exact_lookup=lambda key: self.lookup_exact(
                    namespace="skeleton",
                    project_id="skeleton",
                    key=key,
                    _authorization=_PRIVATE_EXACT_AUTHORIZATION,
                ),
            )
        except CanonicalMemoryImportError as exc:
            raise MemoryGatewayPolicyError(exc.reason_code, str(exc)) from exc
        return self._response(
            namespace=namespace,
            command_suffix="memory.import_canonical_manifest",
            payload=receipt,
        )

    def propose_patch(
        self, *, namespace: str, proposal: Mapping[str, Any], project_id: object = None
    ) -> dict[str, object]:
        namespace = self._authorize_namespace(namespace)
        bound_project_id = self._scope_project_id(namespace, project_id)
        if not isinstance(proposal, Mapping):
            raise MemoryGatewayPolicyError("INVALID_PATCH_PROPOSAL", "proposal must be an object")
        if proposal.get("namespace") != namespace:
            raise MemoryGatewayPolicyError("NAMESPACE_NOT_AUTHORIZED", "proposal namespace mismatch")
        proposal_project_id = self._optional_project_id(proposal.get("project_id"))
        if bound_project_id is not None and proposal_project_id != bound_project_id:
            raise MemoryGatewayPolicyError("PROJECT_NOT_AUTHORIZED", "proposal project_id mismatch")
        self._validate_patch_evidence(namespace, bound_project_id, proposal)
        duplicate_existing = self._patch_registry.lookup_by_idempotency_key(
            str(proposal.get("idempotency_key", ""))
        )
        event = self._patch_registry.propose(proposal)
        self._append_audit_event(
            namespace=namespace,
            project_id=bound_project_id,
            actor_ref=proposal.get("actor_ref"),
            reason_code=proposal.get("reason_code"),
            approval_ref=proposal.get("approval_ref"),
            canonical_revision=proposal.get("confirmed_canonical_revision"),
        )
        return self._response(
            namespace=namespace,
            command_suffix="memory.propose_patch",
            payload={
                "project_id": proposal_project_id,
                "proposal_event": event,
                "idempotency_classification": (
                    "DUPLICATE_EXISTING" if duplicate_existing else "NEW_PROPOSAL"
                ),
            },
        )

    def _validate_patch_evidence(self, namespace: str, project_id: str, proposal: Mapping[str, Any]) -> None:
        provenance_refs = proposal.get("provenance_refs")
        if not isinstance(provenance_refs, list):
            raise MemoryGatewayPolicyError("INVALID_PATCH_PROPOSAL", "provenance refs must be an array")
        has_semantic = any(isinstance(ref, Mapping) and ref.get("kind") == "semantic_only" for ref in provenance_refs)
        has_graph = any(isinstance(ref, Mapping) and ref.get("kind") == "code_graph" for ref in provenance_refs)
        if _has_stale_index_reference(provenance_refs):
            raise MemoryGatewayPolicyError(
                STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE,
                "stale derived index cannot support a patch proposal",
            )
        freshness = self._freshness.get((namespace, project_id), {})
        mempalace_freshness = (
            self._mempalace_index_freshness(namespace, project_id)
            if has_semantic
            else freshness.get("mempalace", {})
        )
        graph_freshness = freshness.get("graphify", {})
        if has_semantic:
            if isinstance(mempalace_freshness, Mapping) and mempalace_freshness.get("stale"):
                raise MemoryGatewayPolicyError(
                    STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE,
                    "stale semantic index cannot support a patch proposal",
                )
            if not proposal.get("confirmed_via_exact_ref"):
                raise MemoryGatewayPolicyError(
                    SEMANTIC_RESULT_NOT_CANON_CONFIRMED,
                    "semantic evidence requires exact canonical confirmation",
                )
        if has_graph:
            if isinstance(graph_freshness, Mapping) and graph_freshness.get("stale"):
                raise MemoryGatewayPolicyError(
                    STALE_INDEX_RESULT_NOT_PATCH_ELIGIBLE,
                    "stale graph index cannot support a patch proposal",
                )
            if not proposal.get("confirmed_via_exact_ref"):
                raise MemoryGatewayPolicyError(
                    GRAPH_RESULT_NOT_CANON_CONFIRMED,
                    "graph evidence requires exact canonical confirmation",
                )
        canonical_freshness = (
            mempalace_freshness
            if _is_mempalace_synthetic_scope(namespace, project_id)
            else freshness.get("canonical_sqlite", {})
        )
        canonical_revision = _current_canonical_revision(canonical_freshness)
        if canonical_revision is not None and proposal.get("confirmed_canonical_revision") != canonical_revision:
            raise MemoryGatewayPolicyError(
                EXACT_CONFIRMATION_REVISION_MISMATCH,
                "exact confirmation revision does not match current canonical revision",
            )
        self._validate_canonical_exact_confirmation(namespace, project_id, proposal)

    def _mempalace_index_freshness(self, namespace: str, project_id: str) -> Mapping[str, object]:
        if not _is_mempalace_synthetic_scope(namespace, project_id):
            return self._freshness.get((namespace, project_id), {}).get("mempalace", {})
        if self._mempalace_adapter is None:
            raise MemoryGatewayPolicyError(
                "MEMPALACE_ADAPTER_REQUIRED",
                "synthetic MemPalace route requires explicit adapter injection",
            )
        try:
            return self._mempalace_adapter.get_index_freshness(
                namespace=namespace,
                project_id=project_id,
            )
        except MemPalaceAdapterError as exc:
            raise MemoryGatewayPolicyError(exc.reason_code, str(exc)) from exc

    def _validate_canonical_exact_confirmation(
        self, namespace: str, project_id: str, proposal: Mapping[str, Any]
    ) -> None:
        target = proposal.get("normalized_target")
        confirmed_ref = proposal.get("confirmed_via_exact_ref")
        source_evidence_hash = proposal.get("source_evidence_hash")
        if not isinstance(target, str) or not isinstance(confirmed_ref, str):
            raise MemoryGatewayPolicyError("INVALID_PATCH_PROPOSAL", "exact confirmation target is invalid")
        canonical_records = self._canonical.get((namespace, project_id), {})
        canonical = canonical_records.get(target)
        canonical_freshness = self._freshness.get((namespace, project_id), {}).get("canonical_sqlite", {})
        current_revision = (
            canonical_freshness.get("current_canonical_revision")
            if isinstance(canonical_freshness, Mapping)
            else None
        )
        if canonical is None or current_revision is None or canonical.get("canonical_revision") != current_revision:
            raise MemoryGatewayPolicyError(
                "EXACT_CONFIRMATION_NOT_CANONICAL",
                "exact confirmation is not bound to a current canonical record",
            )
        canonical_refs = canonical.get("provenance_refs")
        if not isinstance(canonical_refs, list):
            raise MemoryGatewayPolicyError(
                "EXACT_CONFIRMATION_NOT_CANONICAL",
                "canonical exact provenance is unavailable",
            )
        for ref in canonical_refs:
            if (
                isinstance(ref, Mapping)
                and ref.get("kind") == "exact_source"
                and ref.get("ref") == confirmed_ref
                and ref.get("evidence_hash") == source_evidence_hash
            ):
                return
        raise MemoryGatewayPolicyError(
            "EXACT_CONFIRMATION_NOT_CANONICAL",
            "exact confirmation does not match current canonical provenance",
        )

    def _append_audit_event(
        self,
        *,
        namespace: str,
        project_id: str,
        actor_ref: object,
        reason_code: object,
        approval_ref: object,
        canonical_revision: object,
    ) -> None:
        event = {
            "schema": MEMORY_GATEWAY_AUDIT_SCHEMA,
            "namespace": namespace,
            "project_id": project_id,
            "event_ref": f"memory-gateway-audit-{len(self._audit_log) + 1:06d}",
            "actor_ref": sanitized_actor_ref(actor_ref),
            "reason_code": sanitized_reason_code(reason_code),
            "approval_ref": sanitized_actor_ref(approval_ref),
            "canonical_revision": canonical_revision,
        }
        validate_public_payload(event)
        self._audit_log.append(event)

    def _authorize_namespace(self, namespace: object) -> str:
        authorized = validate_namespace(namespace, allowed_namespaces=frozenset(self._token.namespaces))
        if self._token.public_mode and authorized in PUBLIC_MODE_FORBIDDEN_NAMESPACES:
            raise MemoryGatewayPolicyError("PRIVATE_NAMESPACE_PUBLIC_MODE_FORBIDDEN", "namespace is private")
        return authorized

    def _optional_project_id(self, project_id: object) -> str | None:
        if project_id is None:
            return None
        if not isinstance(project_id, str) or not _SAFE_PROJECT_ID_RE.fullmatch(project_id):
            raise MemoryGatewayPolicyError("PROJECT_ID_REQUIRED", "project_id is mandatory")
        if project_id == "*" or "*" in project_id:
            raise MemoryGatewayPolicyError("WILDCARD_PROJECT_FORBIDDEN", "wildcard project access is forbidden")
        validate_public_payload({"project_id": project_id})
        return project_id

    def _scope_project_id(self, namespace: str, project_id: object) -> str:
        return self._optional_project_id(project_id) or namespace

    def _response(self, *, namespace: str, command_suffix: str, payload: Mapping[str, Any]) -> dict[str, object]:
        response = {
            "schema": MEMORY_GATEWAY_RESPONSE_SCHEMA,
            "contract_version": MEMORY_GATEWAY_CONTRACT_VERSION,
            "namespace": namespace,
            "command": command_name(namespace, command_suffix),
            "payload": build_command_receipt(command_suffix, payload),
        }
        json.dumps(response, allow_nan=False, sort_keys=True)
        return response

    def _lookup_sqlite_canonical_exact(
        self,
        *,
        namespace: str,
        project_id: str,
        key: str,
    ) -> dict[str, object] | None:
        if (
            self._skeleton_memory is None
            or namespace != "skeleton"
            or project_id != "skeleton"
            or key != FAST_AUTONOMOUS_EXECUTION_KEY
        ):
            return None
        record = self._skeleton_memory.lookup_canonical_record(
            namespace=CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
            scope=CANONICAL_OPERATOR_PREFERENCES_SCOPE,
            key=FAST_AUTONOMOUS_EXECUTION_KEY,
        )
        if record is None:
            return None
        return {
            "canonical_ref": f"canon-{CANONICAL_OPERATOR_PREFERENCES_NAMESPACE}-{record['version']}",
            "canonical_revision": record["canonical_revision"],
            "created_revision": record["created_revision"],
            "imported_at": record["imported_at"],
            "fact_type": "operator_working_style_preference",
            "canonical_namespace": record["namespace"],
            "scope": record["scope"],
            "key": record["key"],
            "version": record["version"],
            "integrity_hash": record["integrity_hash"],
            "normalized_manifest_json": record["manifest_json"],
            "provenance_refs": [
                {
                    "ref": record["provenance_ref"],
                    "kind": "exact_source",
                    "evidence_hash": record["integrity_hash"],
                }
            ],
        }


def _normalize_token(token: GatewayCapabilityToken | Mapping[str, Any]) -> GatewayCapabilityToken:
    if isinstance(token, GatewayCapabilityToken):
        raw_namespaces = token.namespaces
        public_mode = token.public_mode
    elif isinstance(token, Mapping):
        if token.get("schema") != "skeleton.memory_gateway.capability_token.v1":
            raise MemoryGatewayPolicyError("INVALID_CAPABILITY_TOKEN", "capability token schema is invalid")
        raw_namespaces = token.get("namespaces")
        public_mode = bool(token.get("public_mode", True))
    else:
        raise MemoryGatewayPolicyError("INVALID_CAPABILITY_TOKEN", "capability token must be an object")
    if not isinstance(raw_namespaces, tuple | list) or not raw_namespaces:
        raise MemoryGatewayPolicyError("NAMESPACE_REQUIRED", "capability token requires namespaces")
    namespaces = tuple(
        validate_namespace(namespace, allowed_namespaces=ALLOWED_NAMESPACES)
        for namespace in raw_namespaces
    )
    return GatewayCapabilityToken(
        schema="skeleton.memory_gateway.capability_token.v1",
        namespaces=namespaces,
        public_mode=public_mode,
    )


def _is_mempalace_synthetic_scope(namespace: str, project_id: str) -> bool:
    return namespace == MEMPALACE_SYNTHETIC_NAMESPACE and project_id == MEMPALACE_SYNTHETIC_PROJECT_ID


def _is_graphify_synthetic_scope(namespace: str, project_id: str) -> bool:
    return namespace == GRAPHIFY_SYNTHETIC_NAMESPACE and project_id == GRAPHIFY_SYNTHETIC_PROJECT_ID


def _reject_wrong_graphify_synthetic_scope(namespace: str, project_id: str) -> None:
    if project_id == GRAPHIFY_SYNTHETIC_PROJECT_ID:
        raise MemoryGatewayPolicyError("PROJECT_NOT_AUTHORIZED", "synthetic Graphify route scope mismatch")


def _is_gateway_response(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("schema") == MEMORY_GATEWAY_RESPONSE_SCHEMA


def _has_stale_index_reference(provenance_refs: list[object]) -> bool:
    for ref in provenance_refs:
        if not isinstance(ref, Mapping) or ref.get("kind") not in {"semantic_only", "code_graph"}:
            continue
        if ref.get("stale") is True:
            return True
        indexed_revision = ref.get("indexed_canonical_revision")
        current_revision = ref.get("current_canonical_revision")
        if indexed_revision is not None and current_revision is not None and indexed_revision != current_revision:
            return True
    return False


def _current_canonical_revision(freshness: object) -> object:
    if isinstance(freshness, Mapping):
        return freshness.get("current_canonical_revision")
    return None


def _seed_scopes() -> tuple[tuple[str, str], ...]:
    scopes = [(namespace, namespace) for namespace in ALLOWED_NAMESPACES]
    scopes.extend((("aufmass", "project-a"), ("aufmass", "project-b")))
    return tuple(scopes)


def _canonical_seed() -> dict[tuple[str, str], dict[str, dict[str, object]]]:
    return {
        (namespace, project_id): {
            "primary_fact": {
                "canonical_ref": f"canon-{namespace}-{project_id}-primary",
                "canonical_revision": 3,
                "fact_type": "status",
                "value": {"state": f"ready-{project_id}"},
                "provenance_refs": [
                    {
                        "ref": f"exact-{namespace}-{project_id}-primary",
                        "kind": "exact_source",
                        "evidence_hash": "0" * 64,
                    }
                ],
            }
        }
        for namespace, project_id in _seed_scopes()
    }


def _semantic_seed() -> dict[tuple[str, str], list[dict[str, object]]]:
    return {
        (namespace, project_id): [
            {
                "result_ref": f"semantic-{namespace}-{project_id}-primary",
                "canonical_ref_hint": f"canon-{namespace}-{project_id}-primary",
                "canonical_revision_hint": 3,
                "provenance_refs": [
                    {
                        "ref": f"semantic-{namespace}-{project_id}-summary",
                        "kind": "semantic_only",
                        "evidence_hash": "1" * 64,
                    }
                ],
            }
        ]
        for namespace, project_id in _seed_scopes()
    }


def _graph_seed() -> dict[tuple[str, str], list[dict[str, object]]]:
    return {
        (namespace, project_id): [
            {
                "result_ref": f"graph-{namespace}-{project_id}-primary",
                "canonical_ref_hint": f"canon-{namespace}-{project_id}-primary",
                "canonical_revision_hint": 3,
                "provenance_refs": [
                    {
                        "ref": f"graph-{namespace}-{project_id}-edge",
                        "kind": "code_graph",
                        "evidence_hash": "2" * 64,
                    }
                ],
            }
        ]
        for namespace, project_id in _seed_scopes()
    }


def _freshness_seed() -> dict[tuple[str, str], dict[str, dict[str, object]]]:
    freshness = {}
    for namespace, project_id in _seed_scopes():
        stale = namespace == "bauclock"
        freshness[(namespace, project_id)] = {
            "graphify": {
                "indexed_repo_commit": "commit-indexed-0003",
                "current_repo_commit": "commit-current-0004" if stale else "commit-indexed-0003",
                "indexed_at": "2026-06-27T00:00:00Z",
                "stale": stale,
                "index_namespace": namespace,
                "project_id": project_id,
            },
            "mempalace": {
                "indexed_canonical_revision": 2 if stale else 3,
                "current_canonical_revision": 3,
                "source_snapshot_id": f"snapshot-{namespace}-{project_id}-0003",
                "indexed_at": "2026-06-27T00:00:00Z",
                "stale": stale,
                "index_namespace": namespace,
                "project_id": project_id,
            },
            "canonical_sqlite": {
                "current_canonical_revision": 3,
                "project_id": project_id,
            },
        }
    return freshness


def _safe_lookup_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise MemoryGatewayPolicyError(
            "INVALID_LOOKUP_KEY",
            "query must be a bounded string",
        )
    return value


def _safe_exact_key(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_PROJECT_ID_RE.fullmatch(value):
        raise MemoryGatewayPolicyError(
            "INVALID_LOOKUP_KEY",
            "lookup key must be a bounded identifier",
        )
    return value


def _sanitized_conflict(conflict: Mapping[str, object]) -> dict[str, object]:
    allowed_keys = {
        "schema",
        "status",
        "event_ref",
        "conflict_ref",
        "dedupe_key",
        "namespace",
        "project_id",
        "existing_canonical_ref",
        "existing_event_ref",
        "reason_code",
    }
    return {key: deepcopy(conflict[key]) for key in allowed_keys if key in conflict}


def _record_belongs_to_scope(record: Mapping[str, object], namespace: str, project_id: str) -> bool:
    return record.get("namespace") == namespace and record.get("project_id") == project_id


def allowed_command_names(namespace: str) -> list[str]:
    namespace = validate_namespace(namespace, allowed_namespaces=ALLOWED_NAMESPACES)
    return [command_name(namespace, suffix) for suffix in sorted(ALLOWED_COMMAND_SUFFIXES)]
