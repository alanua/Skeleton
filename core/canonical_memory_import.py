from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable, Mapping

from core.canonical_memory import (
    CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
    CANONICAL_OPERATOR_PREFERENCES_SCOPE,
    FAST_AUTONOMOUS_EXECUTION_KEY,
)
from core.canonical_memory_manifest import (
    canonical_manifest_integrity_hash,
    validate_canonical_memory_manifest,
)
from core.skeleton_memory import SkeletonMemory


APPROVED_APPROVAL_REF = "issue-1194-comment-4846756659"
APPROVED_MANIFEST_VERSION = 1
APPROVED_INTEGRITY_HASH = "68ea3713f2f3d9bfd80215a986e54525cd20db926a0de109c23bfeeed94fbf04"
CANONICAL_IMPORT_RECEIPT_SCHEMA = "skeleton.canonical_memory_import_receipt.v1"


class CanonicalMemoryImportError(ValueError):
    """Raised when a bounded canonical import fails closed."""

    def __init__(self, reason_code: str, message: str, *, rollback_status: str = "not_started") -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.rollback_status = rollback_status


def import_approved_operator_preference_manifest(
    *,
    store: SkeletonMemory,
    manifest: Mapping[str, Any],
    exact_lookup: Callable[[str], Mapping[str, Any]],
) -> dict[str, object]:
    if not isinstance(store, SkeletonMemory):
        raise CanonicalMemoryImportError("TRUSTED_STORE_REQUIRED", "a trusted SkeletonMemory instance is required")
    normalized_manifest = _validated_exact_manifest(manifest)
    store.init_schema()

    rollback_status = "not_started"
    try:
        store.begin_canonical_import_transaction()
        rollback_status = "not_required"
        try:
            snapshot = store.create_canonical_pre_import_snapshot()
        except Exception as exc:
            raise CanonicalMemoryImportError(
                "SNAPSHOT_UNAVAILABLE",
                "pre-import snapshot is unavailable",
                rollback_status="pending",
            ) from exc
        if snapshot.get("status") != "created":
            raise CanonicalMemoryImportError(
                "SNAPSHOT_UNAVAILABLE",
                "pre-import snapshot is unavailable",
                rollback_status="pending",
            )

        existing = store.lookup_canonical_record(
            namespace=CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
            scope=CANONICAL_OPERATOR_PREFERENCES_SCOPE,
            key=FAST_AUTONOMOUS_EXECUTION_KEY,
            version=APPROVED_MANIFEST_VERSION,
        )
        all_existing = store.list_canonical_records_for_key(
            namespace=CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
            scope=CANONICAL_OPERATOR_PREFERENCES_SCOPE,
            key=FAST_AUTONOMOUS_EXECUTION_KEY,
        )
        if existing is not None:
            if (
                existing["integrity_hash"] != APPROVED_INTEGRITY_HASH
                or existing["manifest_json"] != normalized_manifest
                or existing["provenance_ref"] != APPROVED_APPROVAL_REF
            ):
                raise CanonicalMemoryImportError(
                    "CANONICAL_VERSION_CONFLICT",
                    "existing canonical version differs from approved manifest",
                    rollback_status="pending",
                )
            receipt = _receipt(
                idempotency_classification="DUPLICATE_EXISTING",
                canonical_revision=int(existing["canonical_revision"]),
                snapshot_status="created",
                read_back_status="verified",
                rollback_status="not_required",
                authoritative=bool(existing["authoritative"]),
            )
            store.insert_canonical_import_receipt(receipt)
            store.commit_canonical_import_transaction()
            return receipt

        if all_existing:
            raise CanonicalMemoryImportError(
                "CANONICAL_VERSION_CONFLICT",
                "canonical key already has a conflicting version",
                rollback_status="pending",
            )

        canonical_revision = store.next_canonical_revision()
        store.insert_canonical_record(
            namespace=CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
            scope=CANONICAL_OPERATOR_PREFERENCES_SCOPE,
            key=FAST_AUTONOMOUS_EXECUTION_KEY,
            version=APPROVED_MANIFEST_VERSION,
            provenance_ref=APPROVED_APPROVAL_REF,
            supersession=dict(manifest["supersession"]),
            integrity_hash=APPROVED_INTEGRITY_HASH,
            manifest_json=normalized_manifest,
            canonical_revision=canonical_revision,
        )
        read_back = exact_lookup(FAST_AUTONOMOUS_EXECUTION_KEY)
        _verify_gateway_read_back(read_back, normalized_manifest, canonical_revision)
        receipt = _receipt(
            idempotency_classification="NEW_IMPORT",
            canonical_revision=canonical_revision,
            snapshot_status="created",
            read_back_status="verified",
            rollback_status="not_required",
            authoritative=True,
        )
        store.insert_canonical_import_receipt(receipt)
        store.commit_canonical_import_transaction()
        return receipt
    except CanonicalMemoryImportError as exc:
        if rollback_status != "not_started" or exc.rollback_status == "pending":
            rollback_status = _rollback(store)
            exc.rollback_status = rollback_status
        raise
    except Exception as exc:
        if rollback_status != "not_started":
            rollback_status = _rollback(store)
        raise CanonicalMemoryImportError(
            "CANONICAL_IMPORT_FAILED",
            "canonical import failed",
            rollback_status=rollback_status,
        ) from exc


def normalized_manifest_json(manifest: Mapping[str, Any]) -> str:
    return json.dumps(
        deepcopy(dict(manifest)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _validated_exact_manifest(manifest: Mapping[str, Any]) -> str:
    if not isinstance(manifest, Mapping):
        raise CanonicalMemoryImportError("INVALID_CANONICAL_MANIFEST", "manifest must be an object")
    validation = validate_canonical_memory_manifest(manifest)
    if not validation.ok:
        codes = ",".join(issue.code for issue in validation.errors)
        raise CanonicalMemoryImportError("INVALID_CANONICAL_MANIFEST", f"manifest failed validation: {codes}")
    if manifest.get("namespace") != CANONICAL_OPERATOR_PREFERENCES_NAMESPACE:
        raise CanonicalMemoryImportError("CANONICAL_NAMESPACE_MISMATCH", "manifest namespace is not approved")
    if manifest.get("scope") != CANONICAL_OPERATOR_PREFERENCES_SCOPE:
        raise CanonicalMemoryImportError("CANONICAL_SCOPE_MISMATCH", "manifest scope is not approved")
    if manifest.get("key") != FAST_AUTONOMOUS_EXECUTION_KEY:
        raise CanonicalMemoryImportError("CANONICAL_KEY_MISMATCH", "manifest key is not approved")
    if manifest.get("version") != APPROVED_MANIFEST_VERSION:
        raise CanonicalMemoryImportError("CANONICAL_VERSION_MISMATCH", "manifest version is not approved")
    provenance = manifest.get("provenance")
    if not isinstance(provenance, Mapping) or provenance.get("approval_ref") != APPROVED_APPROVAL_REF:
        raise CanonicalMemoryImportError("APPROVAL_PROVENANCE_MISSING", "approval provenance is not approved")
    if manifest.get("authority") != "candidate_manifest_only":
        raise CanonicalMemoryImportError("CANDIDATE_AUTHORITY_REQUIRED", "manifest authority must be candidate-only")
    if manifest.get("integrity_hash") != APPROVED_INTEGRITY_HASH:
        raise CanonicalMemoryImportError("INTEGRITY_HASH_MISMATCH", "integrity hash is not the approved hash")
    if canonical_manifest_integrity_hash(manifest) != APPROVED_INTEGRITY_HASH:
        raise CanonicalMemoryImportError("INTEGRITY_HASH_MISMATCH", "manifest content does not match approved hash")
    return normalized_manifest_json(manifest)


def _verify_gateway_read_back(
    response: Mapping[str, Any],
    normalized_manifest: str,
    canonical_revision: int,
) -> None:
    payload = response.get("payload")
    if not isinstance(payload, Mapping):
        raise CanonicalMemoryImportError("READ_BACK_MISMATCH", "gateway read-back payload is unavailable")
    if payload.get("normalized_manifest_json") != normalized_manifest:
        raise CanonicalMemoryImportError("READ_BACK_MISMATCH", "gateway read-back manifest differs")
    if payload.get("integrity_hash") != APPROVED_INTEGRITY_HASH:
        raise CanonicalMemoryImportError("READ_BACK_MISMATCH", "gateway read-back hash differs")
    if payload.get("canonical_revision") != canonical_revision:
        raise CanonicalMemoryImportError("READ_BACK_MISMATCH", "gateway read-back revision differs")
    if payload.get("authoritative") is not True:
        raise CanonicalMemoryImportError("READ_BACK_MISMATCH", "gateway read-back is not authoritative")


def _receipt(
    *,
    idempotency_classification: str,
    canonical_revision: int,
    snapshot_status: str,
    read_back_status: str,
    rollback_status: str,
    authoritative: bool,
) -> dict[str, object]:
    return {
        "schema": CANONICAL_IMPORT_RECEIPT_SCHEMA,
        "status": "IMPORTED",
        "idempotency_classification": idempotency_classification,
        "namespace_token": CANONICAL_OPERATOR_PREFERENCES_NAMESPACE,
        "scope_token": CANONICAL_OPERATOR_PREFERENCES_SCOPE,
        "key_token": FAST_AUTONOMOUS_EXECUTION_KEY,
        "version": APPROVED_MANIFEST_VERSION,
        "canonical_revision": canonical_revision,
        "integrity_hash": APPROVED_INTEGRITY_HASH,
        "snapshot_status": snapshot_status,
        "read_back_status": read_back_status,
        "rollback_status": rollback_status,
        "authoritative": authoritative,
    }


def _rollback(store: SkeletonMemory) -> str:
    try:
        store.rollback_canonical_import_transaction()
    except Exception:
        return "failed"
    return "completed"
