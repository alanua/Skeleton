from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "PRIVATE_MEMORY_MIGRATION_PACK.md"
SCHEMA_PATHS = {
    "source_inventory": ROOT / "schemas" / "private_memory_source_inventory.schema.json",
    "extraction_manifest": ROOT / "schemas" / "private_memory_extraction_manifest.schema.json",
    "reconciliation_report": ROOT / "schemas" / "private_memory_reconciliation_report.schema.json",
    "approval_packet": ROOT / "schemas" / "private_memory_approval_packet.schema.json",
}

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
HASH_D = "sha256:" + "d" * 64
HASH_E = "sha256:" + "e" * 64
HASH_F = "sha256:" + "f" * 64
NOW = "2026-07-22T00:00:00Z"
TARGET_REVISION = 1753


def load_schema(name: str) -> dict[str, Any]:
    return json.loads(SCHEMA_PATHS[name].read_text(encoding="utf-8"))


def validator(name: str) -> Draft202012Validator:
    schema = load_schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def assert_invalid(name: str, instance: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        validator(name).validate(instance)


def valid_source_inventory() -> dict[str, Any]:
    return {
        "schema": "skeleton.private_memory_source_inventory.v1",
        "inventory_id": "pm-pack.inventory.1753",
        "generated_at": NOW,
        "privacy_boundary": "PUBLIC_SAFE_METADATA_ONLY",
        "project_ref": "project.skeleton",
        "domain_ref": "domain.private-memory",
        "sources": [
            {
                "stable_source_id": "source.chat.001",
                "source_class": "chat_export",
                "project_ref": "project.skeleton",
                "domain_ref": "domain.private-memory",
                "date_range": {"from": None, "until": NOW},
                "privacy_class": "public_safe_metadata",
                "integrity_hash": HASH_A,
                "retention_class": "review-retained",
                "extraction_class": "metadata-only",
                "authority": {
                    "authority_class": "OPERATOR_CONFIRMED",
                    "authority_ref": "authority.issue-1753",
                    "approval_ref": "approval.review-only",
                },
            }
        ],
        "inventory_hash": HASH_B,
    }


def valid_extraction_manifest() -> dict[str, Any]:
    return {
        "schema": "skeleton.private_memory_extraction_manifest.v1",
        "manifest_id": "pm-pack.manifest.1753",
        "generated_at": NOW,
        "source_inventory_ref": "pm-pack.inventory.1753",
        "source_inventory_hash": HASH_B,
        "records": [
            {
                "record_id": "record.synthetic.001",
                "record_type": "operator_preference_ref",
                "namespace_proposal_ref": "namespace.review.skeleton-notes",
                "project_or_domain_ref": "project.skeleton",
                "subject_ref": "subject.synthetic",
                "privacy_class": "PRIVATE",
                "authority_class": "APPROVED_CANON",
                "source_refs": [
                    {"stable_source_id": "source.chat.001", "source_hash": HASH_A}
                ],
                "source_timestamp": NOW,
                "observed_at": NOW,
                "effective_from": None,
                "valid_until": None,
                "canonical_revision_expected": TARGET_REVISION,
                "supersedes": ["record.synthetic.000"],
                "correction_of": None,
                "tombstones": [],
                "confidence": "HIGH",
                "approval_ref": "approval.review-only",
                "content_hash": HASH_C,
            }
        ],
        "manifest_hash": HASH_D,
    }


def valid_reconciliation_report() -> dict[str, Any]:
    return {
        "schema": "skeleton.private_memory_reconciliation_report.v1",
        "report_id": "pm-pack.report.1753",
        "generated_at": NOW,
        "source_inventory_ref": "pm-pack.inventory.1753",
        "source_inventory_hash": HASH_B,
        "extraction_manifest_ref": "pm-pack.manifest.1753",
        "extraction_manifest_hash": HASH_D,
        "target_canonical_revision": TARGET_REVISION,
        "aggregate_counts": {
            "add": 1,
            "update": 0,
            "tombstone": 0,
            "conflict": 0,
            "unchanged": 0,
            "total": 1,
        },
        "records": [
            {
                "stable_record_ref": "record.synthetic.001",
                "operation": "add",
                "current_hash": None,
                "proposed_hash": HASH_C,
                "reason_codes": ["review-only-add"],
                "unresolved_operator_decision": None,
            }
        ],
        "report_hash": HASH_E,
    }


def valid_approval_packet() -> dict[str, Any]:
    return {
        "schema": "skeleton.private_memory_approval_packet.v1",
        "packet_id": "pm-pack.approval.1753",
        "packet_hash": HASH_F,
        "source_inventory_hash": HASH_B,
        "extraction_manifest_hash": HASH_D,
        "reconciliation_report_hash": HASH_E,
        "target_canonical_revision": TARGET_REVISION,
        "namespace_policy_review_ref": "namespace-policy.review-only",
        "import_contract": "skeleton.memory_gateway.compatible_private_import.review_only.v1",
        "requested_operations": [
            {
                "operation": "add",
                "stable_record_ref": "record.synthetic.001",
                "namespace_proposal_ref": "namespace.review.skeleton-notes",
                "proposed_hash": HASH_C,
                "idempotency_key": "idem.operation.synthetic.001",
            }
        ],
        "aggregate_counts": {
            "add": 1,
            "update": 0,
            "tombstone": 0,
            "conflict": 0,
            "unchanged": 0,
            "total": 1,
        },
        "operator_approval": {
            "approval_state": "pending_approval",
            "approval_ref": "approval.review-only",
            "approved_by_ref": None,
            "approved_at": None,
            "expires_at": None,
            "revoked_at": None,
            "revocation_ref": None,
        },
        "idempotency": {
            "packet_idempotency_key": "idem.packet.synthetic.1753",
            "packet_idempotency_hash": HASH_F,
        },
    }


EXAMPLES = {
    "source_inventory": valid_source_inventory,
    "extraction_manifest": valid_extraction_manifest,
    "reconciliation_report": valid_reconciliation_report,
    "approval_packet": valid_approval_packet,
}


@pytest.mark.parametrize("name", sorted(SCHEMA_PATHS))
def test_schema_is_draft_2020_12_and_complete_synthetic_example_validates(name: str) -> None:
    assert load_schema(name)["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    validator(name).validate(EXAMPLES[name]())


@pytest.mark.parametrize(
    ("name", "required"),
    [
        (
            "source_inventory",
            {
                "schema",
                "inventory_id",
                "generated_at",
                "privacy_boundary",
                "project_ref",
                "domain_ref",
                "sources",
                "inventory_hash",
            },
        ),
        (
            "extraction_manifest",
            {
                "schema",
                "manifest_id",
                "generated_at",
                "source_inventory_ref",
                "source_inventory_hash",
                "records",
                "manifest_hash",
            },
        ),
        (
            "reconciliation_report",
            {
                "schema",
                "report_id",
                "generated_at",
                "source_inventory_ref",
                "source_inventory_hash",
                "extraction_manifest_ref",
                "extraction_manifest_hash",
                "target_canonical_revision",
                "aggregate_counts",
                "records",
                "report_hash",
            },
        ),
        (
            "approval_packet",
            {
                "schema",
                "packet_id",
                "packet_hash",
                "source_inventory_hash",
                "extraction_manifest_hash",
                "reconciliation_report_hash",
                "target_canonical_revision",
                "namespace_policy_review_ref",
                "import_contract",
                "requested_operations",
                "aggregate_counts",
                "operator_approval",
                "idempotency",
            },
        ),
    ],
)
def test_every_top_level_required_field_is_enforced(name: str, required: set[str]) -> None:
    schema = load_schema(name)
    assert set(schema["required"]) == required
    for field in required:
        instance = EXAMPLES[name]()
        del instance[field]
        assert_invalid(name, instance)


@pytest.mark.parametrize(
    ("name", "path", "required"),
    [
        (
            "source_inventory",
            ("sources", 0),
            {
                "stable_source_id",
                "source_class",
                "project_ref",
                "domain_ref",
                "date_range",
                "privacy_class",
                "integrity_hash",
                "retention_class",
                "extraction_class",
                "authority",
            },
        ),
        (
            "source_inventory",
            ("sources", 0, "authority"),
            {"authority_class", "authority_ref", "approval_ref"},
        ),
        (
            "extraction_manifest",
            ("records", 0),
            {
                "record_id",
                "record_type",
                "namespace_proposal_ref",
                "project_or_domain_ref",
                "subject_ref",
                "privacy_class",
                "authority_class",
                "source_refs",
                "source_timestamp",
                "observed_at",
                "effective_from",
                "valid_until",
                "canonical_revision_expected",
                "supersedes",
                "correction_of",
                "tombstones",
                "confidence",
                "approval_ref",
                "content_hash",
            },
        ),
        (
            "extraction_manifest",
            ("records", 0, "source_refs", 0),
            {"stable_source_id", "source_hash"},
        ),
        (
            "reconciliation_report",
            ("aggregate_counts",),
            {"add", "update", "tombstone", "conflict", "unchanged", "total"},
        ),
        (
            "reconciliation_report",
            ("records", 0),
            {
                "stable_record_ref",
                "operation",
                "current_hash",
                "proposed_hash",
                "reason_codes",
                "unresolved_operator_decision",
            },
        ),
        (
            "approval_packet",
            ("requested_operations", 0),
            {
                "operation",
                "stable_record_ref",
                "namespace_proposal_ref",
                "proposed_hash",
                "idempotency_key",
            },
        ),
        (
            "approval_packet",
            ("operator_approval",),
            {
                "approval_state",
                "approval_ref",
                "approved_by_ref",
                "approved_at",
                "expires_at",
                "revoked_at",
                "revocation_ref",
            },
        ),
        (
            "approval_packet",
            ("idempotency",),
            {"packet_idempotency_key", "packet_idempotency_hash"},
        ),
    ],
)
def test_every_nested_required_field_is_enforced(
    name: str, path: tuple[str | int, ...], required: set[str]
) -> None:
    for field in required:
        instance = EXAMPLES[name]()
        target = instance
        for part in path:
            target = target[part]
        del target[field]
        assert_invalid(name, instance)


@pytest.mark.parametrize(
    ("name", "paths"),
    [
        ("source_inventory", [(), ("sources", 0), ("sources", 0, "date_range"), ("sources", 0, "authority")]),
        ("extraction_manifest", [(), ("records", 0), ("records", 0, "source_refs", 0)]),
        ("reconciliation_report", [(), ("aggregate_counts",), ("records", 0)]),
        ("approval_packet", [(), ("requested_operations", 0), ("aggregate_counts",), ("operator_approval",), ("idempotency",)]),
    ],
)
def test_unknown_top_level_and_nested_fields_fail(name: str, paths: list[tuple[str | int, ...]]) -> None:
    for path in paths:
        instance = EXAMPLES[name]()
        target = instance
        for part in path:
            target = target[part]
        target["unexpected"] = True
        assert_invalid(name, instance)


@pytest.mark.parametrize("forbidden", ["value", "content", "payload", "raw_value", "local_path", "credentials", "secret"])
def test_raw_values_payloads_paths_credentials_and_url_path_refs_fail(forbidden: str) -> None:
    manifest = valid_extraction_manifest()
    manifest["records"][0][forbidden] = "synthetic-private-placeholder"
    assert_invalid("extraction_manifest", manifest)

    inventory = valid_source_inventory()
    inventory["sources"][0][forbidden] = "synthetic-private-placeholder"
    assert_invalid("source_inventory", inventory)

    manifest = valid_extraction_manifest()
    manifest["records"][0]["subject_ref"] = "https://example.invalid/private"
    assert_invalid("extraction_manifest", manifest)

    inventory = valid_source_inventory()
    inventory["sources"][0]["stable_source_id"] = "/tmp/private-source.json"
    assert_invalid("source_inventory", inventory)


def test_exact_1753_enums_accept_valid_values_and_reject_old_parallel_values() -> None:
    manifest_validator = validator("extraction_manifest")
    for privacy in ["PRIVATE", "RESTRICTED", "SECRET_REF_ONLY"]:
        instance = valid_extraction_manifest()
        instance["records"][0]["privacy_class"] = privacy
        manifest_validator.validate(instance)
    for authority in [
        "OPERATOR_CONFIRMED",
        "APPROVED_CANON",
        "SOURCE_FACT",
        "OPERATIONAL",
        "DERIVED",
        "PROVISIONAL",
    ]:
        instance = valid_extraction_manifest()
        instance["records"][0]["authority_class"] = authority
        manifest_validator.validate(instance)
    for confidence in ["HIGH", "MEDIUM", "LOW"]:
        instance = valid_extraction_manifest()
        instance["records"][0]["confidence"] = confidence
        manifest_validator.validate(instance)

    for field, old_value in [
        ("privacy_class", "LOCAL_PRIVATE"),
        ("authority_class", "candidate_manifest_only"),
        ("confidence", "verified"),
    ]:
        instance = valid_extraction_manifest()
        instance["records"][0][field] = old_value
        assert_invalid("extraction_manifest", instance)


def test_source_inventory_classes_privacy_boundary_and_authority_vocabulary() -> None:
    source_validator = validator("source_inventory")
    for source_class in [
        "chat_export",
        "document_store",
        "repository_issue",
        "email_archive",
        "task_log",
        "operator_note_archive",
        "external_system_export",
    ]:
        instance = valid_source_inventory()
        instance["sources"][0]["source_class"] = source_class
        source_validator.validate(instance)
    for privacy_class in [
        "public_safe_metadata",
        "local_private",
        "restricted_operator_private",
        "confidential",
    ]:
        instance = valid_source_inventory()
        instance["sources"][0]["privacy_class"] = privacy_class
        source_validator.validate(instance)

    instance = valid_source_inventory()
    instance["privacy_boundary"] = "LOCAL_PRIVATE"
    assert_invalid("source_inventory", instance)


def test_nullable_subject_timestamps_revision_and_correction_validate() -> None:
    instance = valid_extraction_manifest()
    record = instance["records"][0]
    record["subject_ref"] = None
    record["source_timestamp"] = None
    record["observed_at"] = None
    record["effective_from"] = None
    record["valid_until"] = None
    record["canonical_revision_expected"] = None
    record["correction_of"] = None
    validator("extraction_manifest").validate(instance)

    instance = valid_extraction_manifest()
    instance["records"][0]["canonical_revision_expected"] = -1
    assert_invalid("extraction_manifest", instance)


def test_unique_link_arrays_are_enforced() -> None:
    instance = valid_extraction_manifest()
    instance["records"][0]["supersedes"] = ["record.synthetic.000", "record.synthetic.000"]
    assert_invalid("extraction_manifest", instance)

    instance = valid_extraction_manifest()
    instance["records"][0]["tombstones"] = ["record.synthetic.002", "record.synthetic.002"]
    assert_invalid("extraction_manifest", instance)


def test_source_refs_are_metadata_only_and_non_empty() -> None:
    instance = valid_extraction_manifest()
    instance["records"][0]["source_refs"] = []
    assert_invalid("extraction_manifest", instance)

    instance = valid_extraction_manifest()
    instance["records"][0]["source_refs"][0]["source_hash"] = "raw-source-value"
    assert_invalid("extraction_manifest", instance)


def test_approved_and_revoked_conditionals_require_evidence() -> None:
    packet_validator = validator("approval_packet")

    approved = valid_approval_packet()
    approved["operator_approval"]["approval_state"] = "approved"
    approved["operator_approval"]["approved_by_ref"] = "operator.synthetic"
    approved["operator_approval"]["approved_at"] = NOW
    packet_validator.validate(approved)

    missing_approved_evidence = deepcopy(approved)
    missing_approved_evidence["operator_approval"]["approved_by_ref"] = None
    assert_invalid("approval_packet", missing_approved_evidence)

    revoked = valid_approval_packet()
    revoked["operator_approval"]["approval_state"] = "revoked"
    revoked["operator_approval"]["revoked_at"] = NOW
    revoked["operator_approval"]["revocation_ref"] = "revocation.synthetic"
    packet_validator.validate(revoked)

    missing_revoked_evidence = deepcopy(revoked)
    missing_revoked_evidence["operator_approval"]["revocation_ref"] = None
    assert_invalid("approval_packet", missing_revoked_evidence)


def test_cross_contract_upstream_hashes_and_target_revision_are_bound_in_synthetic_fixtures() -> None:
    inventory = valid_source_inventory()
    manifest = valid_extraction_manifest()
    report = valid_reconciliation_report()
    packet = valid_approval_packet()

    assert manifest["source_inventory_hash"] == inventory["inventory_hash"]
    assert report["source_inventory_hash"] == inventory["inventory_hash"]
    assert report["extraction_manifest_hash"] == manifest["manifest_hash"]
    assert packet["source_inventory_hash"] == inventory["inventory_hash"]
    assert packet["extraction_manifest_hash"] == manifest["manifest_hash"]
    assert packet["reconciliation_report_hash"] == report["report_hash"]
    assert report["target_canonical_revision"] == TARGET_REVISION
    assert packet["target_canonical_revision"] == TARGET_REVISION
    assert manifest["records"][0]["canonical_revision_expected"] == TARGET_REVISION
    assert packet["requested_operations"][0]["stable_record_ref"] == report["records"][0]["stable_record_ref"]
    assert packet["requested_operations"][0]["proposed_hash"] == report["records"][0]["proposed_hash"]


def test_approval_packet_import_contract_and_states_are_exact() -> None:
    schema = load_schema("approval_packet")
    assert (
        schema["properties"]["import_contract"]["const"]
        == "skeleton.memory_gateway.compatible_private_import.review_only.v1"
    )

    packet_validator = validator("approval_packet")
    for state in ["draft", "pending_approval", "approved", "rejected", "expired", "revoked"]:
        instance = valid_approval_packet()
        instance["operator_approval"]["approval_state"] = state
        if state == "approved":
            instance["operator_approval"]["approved_by_ref"] = "operator.synthetic"
            instance["operator_approval"]["approved_at"] = NOW
        if state == "revoked":
            instance["operator_approval"]["revoked_at"] = NOW
            instance["operator_approval"]["revocation_ref"] = "revocation.synthetic"
        packet_validator.validate(instance)

    instance = valid_approval_packet()
    instance["operator_approval"]["approval_state"] = "operator_approved"
    assert_invalid("approval_packet", instance)


def test_approval_packet_requested_operations_reject_empty_list() -> None:
    instance = valid_approval_packet()
    instance["requested_operations"] = []
    assert_invalid("approval_packet", instance)


@pytest.mark.parametrize("operation", ["conflict", "unchanged"])
def test_approval_packet_requested_operations_reject_non_mutation_outcomes(operation: str) -> None:
    instance = valid_approval_packet()
    instance["requested_operations"][0]["operation"] = operation
    assert_invalid("approval_packet", instance)


def test_approval_packet_requested_operations_reject_null_proposed_hash() -> None:
    instance = valid_approval_packet()
    instance["requested_operations"][0]["proposed_hash"] = None
    assert_invalid("approval_packet", instance)


def test_docs_assert_review_only_fail_closed_1753_and_no_runtime_private_operation() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")
    for phrase in [
        "review-only",
        "No private import or runtime mutation occurred",
        "candidate namespace strategy is review-only",
        "Namespace policy remains unchanged",
        "fails closed",
        "Revision gate",
        "Hash gate",
        "Namespace gate",
        "Approval gate",
        "Idempotency gate",
        "Projection freshness gate",
        "Readback gate",
        "exact #1753 vocabulary",
        "All examples and tests are synthetic",
    ]:
        assert phrase in doc
