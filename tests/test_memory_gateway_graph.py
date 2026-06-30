from __future__ import annotations

import pytest

from core.memory_gateway import (
    GRAPHIFY_SYNTHETIC_PROJECT_ID,
    MemoryGateway,
    capability_token,
)
from core.memory_gateway_policy import MemoryGatewayPolicyError


class SyntheticGraphifyAdapter:
    def query_code(self, *, namespace: str, project_id: str, query: str, limit: int = 5) -> dict[str, object]:
        return {
            "results": [
                {
                    "result_ref": f"adapter-{project_id}-{query}",
                    "namespace": namespace,
                    "project_id": project_id,
                    "source_kind": "graphify",
                }
            ]
        }

    def get_index_freshness(self, *, namespace: str, project_id: str) -> dict[str, object]:
        return {
            "indexed_repo_commit": "adapter-indexed-0001",
            "current_repo_commit": "adapter-indexed-0001",
            "indexed_at": "2026-06-27T00:00:00Z",
            "stale": False,
            "index_namespace": namespace,
            "project_id": project_id,
        }


def test_default_skeleton_graph_query_uses_existing_seeded_route() -> None:
    gw = MemoryGateway(capability_token(namespaces=("skeleton",)))

    payload = gw.query_code(namespace="skeleton", query="primary")["payload"]

    assert payload["results"][0]["result_ref"] == "graph-skeleton-skeleton-primary"
    assert payload["results"][0]["canonical_ref_hint"] == "canon-skeleton-skeleton-primary"
    assert payload["results"][0]["project_id"] == "skeleton"


def test_default_skeleton_graph_freshness_uses_existing_seeded_route() -> None:
    gw = MemoryGateway(capability_token(namespaces=("skeleton",)))

    graphify = gw.get_graph_index_freshness(namespace="skeleton")["payload"]["graphify"]

    assert graphify["index_namespace"] == "skeleton"
    assert graphify["project_id"] == "skeleton"
    assert graphify["indexed_repo_commit"] == "commit-indexed-0003"


def test_injected_graphify_adapter_is_used_only_for_exact_synthetic_scope() -> None:
    gw = MemoryGateway(
        capability_token(namespaces=("skeleton",)),
        graphify_adapter=SyntheticGraphifyAdapter(),
    )

    default_payload = gw.query_code(namespace="skeleton", query="primary")["payload"]
    synthetic_payload = gw.query_code(
        namespace="skeleton",
        project_id=GRAPHIFY_SYNTHETIC_PROJECT_ID,
        query="primary",
    )["payload"]
    synthetic_freshness = gw.get_graph_index_freshness(
        namespace="skeleton",
        project_id=GRAPHIFY_SYNTHETIC_PROJECT_ID,
    )["payload"]["graphify"]

    assert default_payload["results"][0]["result_ref"] == "graph-skeleton-skeleton-primary"
    assert synthetic_payload["results"][0]["result_ref"] == "adapter-graphify_synthetic-primary"
    assert synthetic_freshness["indexed_repo_commit"] == "adapter-indexed-0001"


def test_graphify_synthetic_scope_requires_explicit_adapter() -> None:
    gw = MemoryGateway(capability_token(namespaces=("skeleton",)))

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.query_code(
            namespace="skeleton",
            project_id=GRAPHIFY_SYNTHETIC_PROJECT_ID,
            query="primary",
        )

    assert excinfo.value.reason_code == "GRAPHIFY_ADAPTER_REQUIRED"


def test_wrong_graphify_synthetic_project_fails_closed() -> None:
    gw = MemoryGateway(
        capability_token(namespaces=("skeleton", "aufmass")),
        graphify_adapter=SyntheticGraphifyAdapter(),
    )

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.query_code(
            namespace="aufmass",
            project_id=GRAPHIFY_SYNTHETIC_PROJECT_ID,
            query="primary",
        )

    assert excinfo.value.reason_code == "PROJECT_NOT_AUTHORIZED"
