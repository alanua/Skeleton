from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.private_memory_history import bytes_hash, canonical_json, content_hash, safe_token


PRIVATE_MEMORY_IMPORT_BUNDLE_SCHEMA = "skeleton.private_memory_import_bundle.v1"
PRIVATE_MEMORY_IMPORT_RECEIPT_SCHEMA = "skeleton.private_memory_import_receipt.v1"
PRIVATE_MEMORY_IMPORT_RECEIPT_NAMESPACE = "skeleton.private_memory.import_receipts"
PRIVATE_MEMORY_INBOX_ENV = "SKELETON_PRIVATE_MEMORY_INBOX"
MAX_BUNDLE_BYTES = 2 * 1024 * 1024

_SAFE_BASENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")


class PrivateMemoryBundleError(ValueError):
    """Raised when local-private bundle import must fail closed."""


@dataclass(frozen=True)
class PreparedPrivateMemoryBundle:
    source_path: Path
    inbox_root: Path
    bundle: dict[str, Any]
    bundle_id: str
    file_sha256: str
    bundle_hash: str
    receipt_id: str
    facts: list[dict[str, Any]]
    receipt_fact: dict[str, Any]


def prepare_private_memory_import_bundle(
    *,
    private_root: Path,
    basename: str,
    expected_sha256: str,
    env: Mapping[str, str] | None = None,
) -> PreparedPrivateMemoryBundle:
    source_path, inbox_root = resolve_inbox_bundle(
        private_root=private_root,
        basename=basename,
        expected_sha256=expected_sha256,
        env=env,
    )
    raw = source_path.read_bytes()
    try:
        bundle = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrivateMemoryBundleError("invalid bundle JSON") from exc
    return validate_import_bundle(bundle, source_path=source_path, inbox_root=inbox_root)


def resolve_inbox_bundle(
    *,
    private_root: Path,
    basename: str,
    expected_sha256: str,
    env: Mapping[str, str] | None = None,
) -> tuple[Path, Path]:
    if not isinstance(basename, str) or not _SAFE_BASENAME_RE.fullmatch(basename):
        raise PrivateMemoryBundleError("bundle name must be a safe basename")
    if "/" in basename or "\\" in basename or basename in {".", ".."} or ".." in basename:
        raise PrivateMemoryBundleError("bundle name must not traverse")
    if not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(expected_sha256):
        raise PrivateMemoryBundleError("expected sha256 must be explicit hex")

    env_map = env if env is not None else os.environ
    inbox_root = Path(env_map.get(PRIVATE_MEMORY_INBOX_ENV, private_root / "inbox")).expanduser().resolve()
    source_path = (inbox_root / basename).resolve()
    if not source_path.is_relative_to(inbox_root):
        raise PrivateMemoryBundleError("bundle must stay inside inbox")
    _require_private_parent(inbox_root)

    try:
        link_stat = source_path.lstat()
    except FileNotFoundError as exc:
        raise PrivateMemoryBundleError("bundle unavailable") from exc
    if stat.S_ISLNK(link_stat.st_mode):
        raise PrivateMemoryBundleError("bundle must not be a symlink")
    if not stat.S_ISREG(link_stat.st_mode):
        raise PrivateMemoryBundleError("bundle must be a regular file")
    if getattr(link_stat, "st_nlink", 1) != 1:
        raise PrivateMemoryBundleError("bundle must not have hard-link surprises")
    if stat.S_IMODE(link_stat.st_mode) & 0o177:
        raise PrivateMemoryBundleError("bundle file mode is too broad")
    if link_stat.st_size > MAX_BUNDLE_BYTES:
        raise PrivateMemoryBundleError("bundle exceeds maximum size")

    canonical_stat = source_path.stat()
    if (canonical_stat.st_dev, canonical_stat.st_ino) != (link_stat.st_dev, link_stat.st_ino):
        raise PrivateMemoryBundleError("bundle path changed during validation")
    digest = bytes_hash(source_path.read_bytes())
    if digest.lower() != expected_sha256.lower():
        raise PrivateMemoryBundleError("bundle sha256 mismatch")
    return source_path, inbox_root


def validate_import_bundle(
    bundle: object,
    *,
    source_path: Path,
    inbox_root: Path,
) -> PreparedPrivateMemoryBundle:
    if not isinstance(bundle, dict):
        raise PrivateMemoryBundleError("bundle must be an object")
    required = {
        "schema",
        "bundle_id",
        "privacy_class",
        "operator_approved",
        "record_count",
        "records",
    }
    if required - set(bundle):
        raise PrivateMemoryBundleError("bundle missing required fields")
    if bundle.get("schema") != PRIVATE_MEMORY_IMPORT_BUNDLE_SCHEMA:
        raise PrivateMemoryBundleError("unsupported bundle schema")
    if bundle.get("privacy_class") != "LOCAL_PRIVATE":
        raise PrivateMemoryBundleError("bundle privacy class blocked")
    if bundle.get("operator_approved") is not True:
        raise PrivateMemoryBundleError("bundle requires operator approval")
    bundle_id = safe_token(str(bundle.get("bundle_id")), "bundle_id")
    records = bundle.get("records")
    if not isinstance(records, list):
        raise PrivateMemoryBundleError("records must be a list")
    if bundle.get("record_count") != len(records):
        raise PrivateMemoryBundleError("record_count mismatch")

    seen: set[tuple[str, str]] = set()
    facts: list[dict[str, Any]] = []
    imported_refs: list[dict[str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            raise PrivateMemoryBundleError("record must be an object")
        missing = {"namespace", "fact_id", "actor", "reason", "approval", "value"} - set(record)
        if missing:
            raise PrivateMemoryBundleError("record missing required fields")
        namespace = safe_token(str(record["namespace"]), "namespace")
        fact_id = safe_token(str(record["fact_id"]), "fact_id")
        actor = safe_token(str(record["actor"]), "actor")
        reason = safe_token(str(record["reason"]), "reason")
        approval = safe_token(str(record["approval"]), "approval")
        value = record["value"]
        try:
            canonical_json(value)
        except (TypeError, ValueError) as exc:
            raise PrivateMemoryBundleError("record value must be JSON serializable") from exc
        key = (namespace, fact_id)
        if key in seen:
            raise PrivateMemoryBundleError("duplicate record canonical ref")
        seen.add(key)
        value_hash = content_hash(value)
        facts.append(
            {
                "namespace": namespace,
                "fact_id": fact_id,
                "value": value,
                "actor": actor,
                "reason": reason,
                "approval": approval,
                "value_hash": value_hash,
            }
        )
        imported_refs.append(
            {
                "canonical_ref": f"{namespace}:{fact_id}",
                "namespace": namespace,
                "fact_id": fact_id,
                "value_hash": value_hash,
            }
        )

    bundle_hash = content_hash(bundle)
    file_sha256 = bytes_hash(source_path.read_bytes())
    receipt_id = content_hash({"bundle_hash": bundle_hash, "bundle_id": bundle_id})
    receipt_value = {
        "schema": PRIVATE_MEMORY_IMPORT_RECEIPT_SCHEMA,
        "bundle_id": bundle_id,
        "bundle_hash": bundle_hash,
        "record_count": len(facts),
        "operator_approved": True,
        "privacy_class": "LOCAL_PRIVATE",
        "actor_refs": sorted({str(fact["actor"]) for fact in facts}),
        "approval_refs": sorted({str(fact["approval"]) for fact in facts}),
        "imported_canonical_refs": sorted(imported_refs, key=lambda item: item["canonical_ref"]),
        "receipt_id": receipt_id,
    }
    receipt_fact = {
        "namespace": PRIVATE_MEMORY_IMPORT_RECEIPT_NAMESPACE,
        "fact_id": bundle_id,
        "value": receipt_value,
    }
    return PreparedPrivateMemoryBundle(
        source_path=source_path,
        inbox_root=inbox_root,
        bundle=dict(bundle),
        bundle_id=bundle_id,
        file_sha256=file_sha256,
        bundle_hash=bundle_hash,
        receipt_id=receipt_id,
        facts=facts,
        receipt_fact=receipt_fact,
    )


def move_processed_bundle(source_path: Path, *, receipt_id: str) -> str:
    processed = source_path.parent / "processed"
    processed.mkdir(mode=0o700, parents=True, exist_ok=True)
    processed.chmod(0o700)
    target = processed / f"{safe_token(receipt_id, 'receipt_id')}.json"
    if target.exists():
        target = processed / f"{safe_token(receipt_id, 'receipt_id')}-{uuid.uuid4().hex}.json"
    os.replace(source_path, target)
    target.chmod(0o600)
    return target.name


def make_pre_operation_snapshot(db_path: Path, root: Path, create_snapshot_fn: Any) -> tuple[dict[str, object], Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix=".pre-import-", dir=root))
    temp_dir.chmod(0o700)
    try:
        snapshot_id = f"pre-import-{uuid.uuid4().hex}"
        manifest = create_snapshot_fn(db_path, temp_dir, snapshot_id=snapshot_id)
        return {"manifest": manifest, "snapshot_path": temp_dir / f"{snapshot_id}.sqlite"}, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def cleanup_pre_operation_snapshot(temp_dir: Path | None) -> None:
    if temp_dir is not None:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _require_private_parent(parent: Path) -> None:
    if not parent.is_dir():
        raise PrivateMemoryBundleError("inbox parent unavailable")
    mode = stat.S_IMODE(parent.stat().st_mode)
    if mode & 0o077:
        raise PrivateMemoryBundleError("inbox parent mode is too broad")
