from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.graphify_adapter import LocalGraphifyIndex
from core.memory_gateway import MemoryGateway, capability_token
from core.memory_gateway_policy import MemoryGatewayPolicyError
from core.memory_gateway_storage import PrivateMemoryGatewayStorage
from core.memory_lifecycle import (
    MEMORY_LIFECYCLE_EVENT_SCHEMA,
    MemoryLifecycleError,
    capture_after_event,
    recall_before_task,
)
from core.private_memory_stack import PrivateMemoryStack


def test_pre_task_recall_uses_gateway_bounded_exact_context(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    _capture(
        gateway,
        domain="runner",
        fact_id="pref-alpha",
        classification="preference",
        payload={"summary": "alpha private runtime preference", "tags": ["alpha"]},
    )

    result = recall_before_task(
        gateway,
        operator_id="operator",
        domain="runner",
        task_route="runner",
        query="alpha",
        limit=1,
    )
    receipt_json = json.dumps(result.public_receipt(), sort_keys=True)

    assert result.status == "DONE"
    assert result.private_payload["values"][0]["value"]["payload"]["summary"] == "alpha private runtime preference"
    assert result.public_receipt()["selected_canonical_refs"] == ["runner.context:pref-alpha"]
    assert "alpha private runtime preference" not in receipt_json


def test_post_event_capture_writes_typed_gateway_request_and_revision_once(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)

    first = _capture(
        gateway,
        domain="documents",
        fact_id="doc-1",
        classification="document_metadata",
        payload={"title": "synthetic invoice", "page_count": 2},
        idempotency_key="doc_capture_1",
    )
    second = _capture(
        gateway,
        domain="documents",
        fact_id="doc-1",
        classification="document_metadata",
        payload={"title": "synthetic invoice", "page_count": 2},
        idempotency_key="doc_capture_1",
    )

    assert first.public_receipt()["canonical_ref"] == "documents.metadata:doc-1"
    assert second.public_receipt()["canonical_revision"] == first.public_receipt()["canonical_revision"]
    assert second.public_receipt()["idempotency_classification"] == "DUPLICATE_IDENTICAL"


def test_cross_domain_operator_knowledge_keeps_namespace_privacy_separation(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    _capture(
        gateway,
        domain="runner",
        fact_id="shared-preference",
        classification="preference",
        payload={"summary": "operator likes concise receipts", "tags": ["shared"]},
    )
    _capture(
        gateway,
        domain="travel",
        fact_id="trip-state",
        classification="project_state",
        payload={"summary": "travel project shared marker", "tags": ["shared"]},
    )

    runner = recall_before_task(
        gateway,
        operator_id="operator",
        domain="runner",
        task_route="runner",
        query="shared",
    )
    travel = recall_before_task(
        gateway,
        operator_id="operator",
        domain="travel",
        task_route="planner",
        query="shared",
    )

    assert runner.public_receipt()["selected_canonical_refs"] == ["runner.context:shared-preference"]
    assert travel.public_receipt()["selected_canonical_refs"] == ["travel.context:trip-state"]


def test_correction_supersession_updates_same_fact_and_advances_one_revision(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    original = _capture(
        gateway,
        domain="home_edge",
        fact_id="device-state",
        classification="device_state_change",
        payload={"device": "synthetic-switch", "state": "off"},
        idempotency_key="device_state_1",
    )
    corrected = _capture(
        gateway,
        domain="home_edge",
        fact_id="device-state",
        classification="device_state_change",
        payload={"device": "synthetic-switch", "state": "on"},
        supersedes="home_edge.devices:device-state",
        idempotency_key="device_state_2",
    )

    assert corrected.public_receipt()["canonical_revision"] == original.public_receipt()["canonical_revision"] + 1
    exact = recall_before_task(
        gateway,
        operator_id="operator",
        domain="home_edge",
        task_route="home_edge",
        query="synthetic-switch",
    ).private_payload["values"][0]["value"]
    assert exact["payload"]["state"] == "on"
    assert exact["supersedes"] == "home_edge.devices:device-state"


def test_derived_index_failure_keeps_canonical_commit_and_returns_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _gateway(tmp_path)

    def fail_rebuild(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("synthetic graph failure")

    monkeypatch.setattr(LocalGraphifyIndex, "rebuild_from_facts", fail_rebuild)

    result = _capture(
        gateway,
        domain="aufmass",
        fact_id="room-state",
        classification="project_state",
        payload={"summary": "aufmass accepted state", "tags": ["room"]},
    )

    assert result.status == "DEGRADED"
    assert result.public_receipt()["canonical_ref"] == "aufmass.context:room-state"
    assert "graphify" in result.public_receipt()["degraded_indexes"]


def test_arbitrary_bounded_private_json_is_accepted_without_vocabulary_filter(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    payload = {
        "note": "password token /home/example/data.sqlite is synthetic fixture text",
        "nested": {"items": [1, True, None, {"unicode": "дозволено"}]},
    }

    result = _capture(
        gateway,
        domain="life_archive",
        fact_id="free-json",
        classification="identity_context_fact",
        payload=payload,
    )

    assert result.public_receipt()["canonical_ref"] == "life_archive.context:free-json"
    assert "synthetic fixture text" not in json.dumps(result.public_receipt(), sort_keys=True)


def test_unknown_scope_schema_secret_and_public_mode_fail_closed(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    with pytest.raises(MemoryLifecycleError) as domain_exc:
        _capture(gateway, domain="future_unknown", fact_id="x", classification="preference", payload={"x": 1})
    assert domain_exc.value.reason_code == "UNKNOWN_DOMAIN_SCOPE"

    with pytest.raises(MemoryLifecycleError) as schema_exc:
        capture_after_event(gateway, {"schema": "wrong"}, operator_id="operator", domain="runner")
    assert schema_exc.value.reason_code == "INVALID_LIFECYCLE_EVENT_SCHEMA"

    with pytest.raises(MemoryLifecycleError) as namespace_exc:
        capture_after_event(
            gateway,
            {
                "schema": MEMORY_LIFECYCLE_EVENT_SCHEMA,
                "event_type": "completed_action",
                "classification": "project_state",
                "fact_namespace": "unknown.context",
                "fact_id": "x",
                "payload": {"x": 1},
                "provenance": {"kind": "synthetic_test", "evidence_hash": "a" * 64},
            },
            operator_id="operator",
            domain="runner",
        )
    assert namespace_exc.value.reason_code == "UNKNOWN_NAMESPACE_SCOPE"

    with pytest.raises(MemoryLifecycleError) as approval_exc:
        capture_after_event(
            gateway,
            {
                "schema": MEMORY_LIFECYCLE_EVENT_SCHEMA,
                "event_type": "completed_action",
                "classification": "project_state",
                "fact_id": "x",
                "payload": {"x": 1},
                "provenance": {"kind": "synthetic_test", "evidence_hash": "a" * 64},
                "approval_ref": "bad/approval",
            },
            operator_id="operator",
            domain="runner",
        )
    assert approval_exc.value.reason_code == "INVALID_APPROVAL_REF"

    with pytest.raises(MemoryLifecycleError) as privacy_exc:
        _capture(
            gateway,
            domain="runner",
            fact_id="secret",
            classification="configuration",
            payload={"secret": "synthetic"},
            privacy_class="secret",
        )
    assert privacy_exc.value.reason_code == "PRIVACY_CLASS_REQUIRES_AUTHORIZED_BOUNDARY"

    public_gateway = MemoryGateway(capability_token(namespaces=("skeleton",), public_mode=True))
    with pytest.raises(MemoryGatewayPolicyError):
        _capture(public_gateway, domain="runner", fact_id="x", classification="preference", payload={"x": 1})


def test_public_receipts_do_not_contain_private_values_sources_secrets_or_paths(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    result = _capture(
        gateway,
        domain="runner",
        fact_id="receipt-safe",
        classification="completed_action_outcome",
        payload={"summary": "secret /tmp/private.sqlite raw source"},
    )
    serialized = json.dumps(result.public_receipt(), sort_keys=True)

    assert "secret" not in serialized.lower()
    assert "/tmp/private.sqlite" not in serialized
    assert "raw source" not in serialized
    assert "payload" not in serialized


def _gateway(tmp_path: Path) -> MemoryGateway:
    stack = PrivateMemoryStack(tmp_path)
    stack.init(import_manifest=False)
    return MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )


def _capture(
    gateway: MemoryGateway,
    *,
    domain: str,
    fact_id: str,
    classification: str,
    payload: object,
    privacy_class: str = "private",
    idempotency_key: str | None = None,
    supersedes: str | None = None,
):
    event = {
        "schema": MEMORY_LIFECYCLE_EVENT_SCHEMA,
        "event_type": "remember_this" if classification == "preference" else "completed_action",
        "classification": classification,
        "fact_id": fact_id,
        "privacy_class": privacy_class,
        "confidence": 0.95,
        "payload": payload,
        "provenance": {"kind": "synthetic_test", "evidence_hash": "a" * 64},
        "approval_ref": "automatic_lifecycle",
        "actor_ref": "operator",
        "reason_code": "automatic_capture",
    }
    if idempotency_key is not None:
        event["idempotency_key"] = idempotency_key
    if supersedes is not None:
        event["supersedes"] = supersedes
    return capture_after_event(gateway, event, operator_id="operator", domain=domain)
