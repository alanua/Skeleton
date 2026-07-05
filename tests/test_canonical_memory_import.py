from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest

from core.canonical_memory import (
    CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
    CANONICAL_OPERATOR_PREFERENCES_SCOPE,
    FAST_AUTONOMOUS_EXECUTION_KEY,
)
from core.canonical_memory_manifest import canonical_manifest_integrity_hash
from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway, capability_token
from core.memory_gateway_policy import MemoryGatewayPolicyError
from core.skeleton_memory import SkeletonMemory


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "canonical_memory" / "operator_preferences_fast_autonomous_execution_v1.json"
RECEIPT_SCHEMA_PATH = ROOT / "schemas" / "canonical_memory_import_receipt.schema.json"


def load_manifest() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def request(payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
        "namespace": "skeleton",
        "command": "skeleton.memory.import_canonical_manifest",
        "payload": payload,
    }


def gateway(memory: SkeletonMemory | None = None) -> MemoryGateway:
    return MemoryGateway(capability_token(namespaces=("skeleton",)), skeleton_memory=memory)


def import_manifest(memory: SkeletonMemory, manifest: dict[str, object] | None = None) -> dict[str, object]:
    return gateway(memory).execute(request({"manifest": manifest or load_manifest()}))["payload"]


def import_receipts(memory: SkeletonMemory) -> list[dict[str, object]]:
    rows = memory.connection.execute(
        "SELECT receipt_json FROM canonical_import_receipts ORDER BY created_at, id"
    ).fetchall()
    return [json.loads(row["receipt_json"]) for row in rows]


def test_exact_approved_manifest_imports_and_reads_back_authoritative() -> None:
    memory = SkeletonMemory()
    memory.init_schema()
    manifest = load_manifest()

    receipt = import_manifest(memory, manifest)
    exact = gateway(memory).lookup_exact(
        namespace="skeleton",
        project_id="skeleton",
        key=FAST_AUTONOMOUS_EXECUTION_KEY,
    )["payload"]

    assert receipt["status"] == "IMPORTED"
    assert receipt["idempotency_classification"] == "NEW_IMPORT"
    assert receipt["canonical_revision"] == 1
    assert receipt["snapshot_status"] == "created"
    assert receipt["read_back_status"] == "verified"
    assert receipt["rollback_status"] == "not_required"
    assert receipt["authoritative"] is True
    assert exact["authoritative"] is True
    assert exact["canonical_revision"] == receipt["canonical_revision"]
    assert exact["integrity_hash"] == manifest["integrity_hash"]
    assert "normalized_manifest_json" not in exact
    assert import_receipts(memory) == [receipt]


def test_persisted_import_receipt_matches_schema_contract() -> None:
    memory = SkeletonMemory()
    receipt = import_manifest(memory)
    persisted = import_receipts(memory)
    schema = json.loads(RECEIPT_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert persisted == [receipt]
    jsonschema.Draft202012Validator(schema).validate(persisted[0])


def test_duplicate_exact_import_is_idempotent() -> None:
    memory = SkeletonMemory()
    memory.init_schema()

    first = import_manifest(memory)
    second = import_manifest(memory)

    assert first["canonical_revision"] == second["canonical_revision"]
    assert second["idempotency_classification"] == "DUPLICATE_EXISTING"
    assert import_receipts(memory) == [first]
    rows = memory.list_canonical_records_for_key(
        namespace=CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
        scope=CANONICAL_OPERATOR_PREFERENCES_SCOPE,
        key=FAST_AUTONOMOUS_EXECUTION_KEY,
    )
    assert len(rows) == 1


def test_modified_statement_with_recomputed_hash_is_rejected() -> None:
    memory = SkeletonMemory()
    memory.init_schema()
    manifest = load_manifest()
    manifest["record"]["operating_rules"][0]["statement"] = "Changed."  # type: ignore[index]
    manifest["integrity_hash"] = canonical_manifest_integrity_hash(manifest)

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        import_manifest(memory, manifest)

    assert excinfo.value.reason_code == "INVALID_CANONICAL_MANIFEST"
    assert memory.lookup_canonical_record(
        namespace=CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
        scope=CANONICAL_OPERATOR_PREFERENCES_SCOPE,
        key=FAST_AUTONOMOUS_EXECUTION_KEY,
    ) is None


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("provenance", "approval_ref"), "issue-1194-comment-0"),
        (("namespace",), "skeleton.other"),
        (("scope",), "project_operator_working_style"),
        (("key",), "other_key"),
        (("version",), 2),
        (("integrity_hash",), "0" * 64),
    ],
)
def test_approval_namespace_scope_key_version_and_integrity_mismatch_are_rejected(
    path: tuple[str, ...],
    value: object,
) -> None:
    memory = SkeletonMemory()
    memory.init_schema()
    manifest = load_manifest()
    target = manifest
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    if path[-1] != "integrity_hash":
        manifest["integrity_hash"] = canonical_manifest_integrity_hash(manifest)

    with pytest.raises(MemoryGatewayPolicyError):
        import_manifest(memory, manifest)

    assert memory.list_canonical_records_for_key(
        namespace=CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
        scope=CANONICAL_OPERATOR_PREFERENCES_SCOPE,
        key=FAST_AUTONOMOUS_EXECUTION_KEY,
    ) == []


def test_conflicting_existing_version_is_rejected_without_mutation() -> None:
    memory = SkeletonMemory()
    memory.init_schema()
    manifest = load_manifest()
    memory.begin_canonical_import_transaction()
    try:
        memory.create_canonical_pre_import_snapshot()
        memory.insert_canonical_record(
            namespace=CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
            scope=CANONICAL_OPERATOR_PREFERENCES_SCOPE,
            key=FAST_AUTONOMOUS_EXECUTION_KEY,
            version=2,
            provenance_ref="issue-1194-comment-4846756659",
            supersession={"status": "supersedes", "supersedes": ["fast_autonomous_execution_v1"]},
            integrity_hash="1" * 64,
            manifest_json=json.dumps({"version": 2}, sort_keys=True),
            canonical_revision=1,
        )
        memory.commit_canonical_import_transaction()
    except Exception:
        memory.rollback_canonical_import_transaction()
        raise

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        import_manifest(memory, manifest)

    assert excinfo.value.reason_code == "CANONICAL_VERSION_CONFLICT"
    rows = memory.list_canonical_records_for_key(
        namespace=CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
        scope=CANONICAL_OPERATOR_PREFERENCES_SCOPE,
        key=FAST_AUTONOMOUS_EXECUTION_KEY,
    )
    assert len(rows) == 1
    assert rows[0]["version"] == 2


def test_snapshot_transaction_failure_blocks_before_mutation() -> None:
    class SnapshotFailMemory(SkeletonMemory):
        def create_canonical_pre_import_snapshot(self) -> dict[str, object]:
            raise RuntimeError("snapshot unavailable")

    memory = SnapshotFailMemory()

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        import_manifest(memory)

    assert excinfo.value.reason_code == "SNAPSHOT_UNAVAILABLE"
    assert memory.list_canonical_records_for_key(
        namespace=CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
        scope=CANONICAL_OPERATOR_PREFERENCES_SCOPE,
        key=FAST_AUTONOMOUS_EXECUTION_KEY,
    ) == []


def test_simulated_write_failure_rolls_back_prior_state() -> None:
    class WriteFailMemory(SkeletonMemory):
        def insert_canonical_record(self, **kwargs: object) -> dict[str, object]:
            super().insert_canonical_record(**kwargs)
            raise RuntimeError("write failed after mutation")

    memory = WriteFailMemory()

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        import_manifest(memory)

    assert excinfo.value.reason_code == "CANONICAL_IMPORT_FAILED"
    assert memory.list_canonical_records_for_key(
        namespace=CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
        scope=CANONICAL_OPERATOR_PREFERENCES_SCOPE,
        key=FAST_AUTONOMOUS_EXECUTION_KEY,
    ) == []
    assert import_receipts(memory) == []


def test_simulated_receipt_write_failure_rolls_back_prior_state() -> None:
    class ReceiptWriteFailMemory(SkeletonMemory):
        def insert_canonical_import_receipt(self, receipt: dict[str, object]) -> None:  # type: ignore[override]
            raise RuntimeError("receipt write failed")

    memory = ReceiptWriteFailMemory()

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        import_manifest(memory)

    assert excinfo.value.reason_code == "CANONICAL_IMPORT_FAILED"
    assert memory.list_canonical_records_for_key(
        namespace=CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
        scope=CANONICAL_OPERATOR_PREFERENCES_SCOPE,
        key=FAST_AUTONOMOUS_EXECUTION_KEY,
    ) == []
    assert import_receipts(memory) == []


def test_read_back_mismatch_rolls_back_prior_state() -> None:
    class ReadBackMismatchGateway(MemoryGateway):
        def lookup_exact(self, **kwargs: object) -> dict[str, object]:
            result = super().lookup_exact(**kwargs)
            result = deepcopy(result)
            result["payload"]["integrity_hash"] = "0" * 64  # type: ignore[index]
            return result

    memory = SkeletonMemory()
    gw = ReadBackMismatchGateway(capability_token(namespaces=("skeleton",)), skeleton_memory=memory)

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gw.execute(request({"manifest": load_manifest()}))

    assert excinfo.value.reason_code == "READ_BACK_MISMATCH"
    assert memory.list_canonical_records_for_key(
        namespace=CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
        scope=CANONICAL_OPERATOR_PREFERENCES_SCOPE,
        key=FAST_AUTONOMOUS_EXECUTION_KEY,
    ) == []
    assert import_receipts(memory) == []


def test_receipt_contains_no_private_or_manifest_body_values() -> None:
    receipt = import_manifest(SkeletonMemory())
    serialized = json.dumps(receipt, sort_keys=True).lower()

    assert "database" not in serialized
    assert "sqlite" not in serialized
    assert "select " not in serialized
    assert "insert " not in serialized
    assert "raw_transcript" not in serialized
    assert "/tmp" not in serialized
    assert "/home" not in serialized
    assert "secret" not in serialized
    assert "customer" not in serialized
    assert "operating_rules" not in serialized


def test_callers_cannot_inject_path_sql_command_environment_or_arbitrary_scope() -> None:
    memory = SkeletonMemory()
    manifest = load_manifest()

    for payload in (
        {"manifest": manifest, "path": "/tmp/private.db"},
        {"manifest": manifest, "sql": "DROP TABLE canonical_memory_records"},
        {"manifest": manifest, "command": "shell"},
        {"manifest": manifest, "environment": {"TOKEN": "secret"}},
        {"manifest": manifest, "namespace": "other", "key": "other"},
    ):
        result = gateway(memory).execute(request(payload))
        assert result["payload"]["namespace_token"] == CANONICAL_OPERATOR_PREFERENCES_NAMESPACE
        assert result["payload"]["key_token"] == FAST_AUTONOMOUS_EXECUTION_KEY

    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gateway(memory).execute(
            {
                "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                "namespace": "skeleton",
                "command": "skeleton.memory.import_canonical_manifest",
                "payload": {"manifest": manifest, "project_id": "other"},
            }
        )

    assert excinfo.value.reason_code == "PROJECT_NOT_AUTHORIZED"


def test_import_requires_injected_skeleton_memory_store() -> None:
    with pytest.raises(MemoryGatewayPolicyError) as excinfo:
        gateway(None).execute(request({"manifest": load_manifest()}))

    assert excinfo.value.reason_code == "TRUSTED_STORE_REQUIRED"
