from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import time
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from core.memory_gateway import MEMORY_GATEWAY_REQUEST_SCHEMA, MemoryGateway, capability_token
from core.memory_gateway_policy import MemoryGatewayPolicyError
from core.memory_gateway_storage import (
    PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
    PrivateMemoryGatewayStorage,
)
from core.private_memory_history import content_hash, current_revision, safe_token, sanitized_integrity_report
from core.private_memory_stack import PRIVATE_MEMORY_STACK_ROOT_ENV, PrivateMemoryStack


AUDIT_SCHEMA = "skeleton.private_memory_fragment_audit.v1"
PLAN_SCHEMA = "skeleton.private_memory_fragment_migration_plan.v1"
APPLY_RECEIPT_SCHEMA = "skeleton.private_memory_fragment_migration_receipt.v1"
LEDGER_SCHEMA = "skeleton.private_memory_fragment_migration_ledger.v1"
REPORT_TEMPLATE_SCHEMA = "skeleton.private_memory_fragment_migration_public_report_template.v1"

CLASSIFICATIONS = (
    "CANONICAL_ALREADY",
    "DERIVED",
    "MIGRATE_DURABLE_FACTS",
    "KEEP_AS_ARTIFACT",
    "OPERATIONAL_STATE_ONLY",
    "DUPLICATE",
    "STALE",
    "SECRET_OR_RESTRICTED",
    "NEEDS_OPERATOR",
)
GENERATED_PRIVATE_MEMORY_NAMES = {
    "fragmented_memory_migration_ledger.sqlite",
    "memory_gateway_mutations.sqlite",
    "canonical_private_memory.sqlite-shm",
    "canonical_private_memory.sqlite-wal",
}
TEXT_SUFFIXES = {".md", ".txt", ".yaml", ".yml", ".json", ".jsonl", ".csv"}
STRUCTURED_SUFFIXES = {".yaml", ".yml", ".json", ".jsonl", ".csv"}
SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}
RAW_ARTIFACT_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".heic",
    ".tif",
    ".tiff",
    ".mp4",
    ".mov",
    ".mp3",
    ".wav",
    ".zip",
    ".tar",
    ".gz",
}
SECRET_RE = re.compile(
    r"(?i)(secret|token|credential|password|passwd|api[_-]?key|apikey|private[_-]?key|"
    r"authorization|bearer|ssh-rsa|BEGIN [A-Z ]*PRIVATE KEY|xox[baprs]-|ghp_[A-Za-z0-9])"
)
OPERATIONAL_RE = re.compile(r"(?i)(runner|loop|lease|queue|state|receipt|checkpoint|session)")
DERIVED_RE = re.compile(r"(?i)(mempalace|cognee|graphify|projection|index)")
STALE_RE = re.compile(r"(?i)(archive|archived|legacy|old|deprecated|stale)")
CANONICAL_RE = re.compile(r"(?i)(canonical[_-]?memory|private_memory_facts|private_memory_events)")
SUPPORTED_NAMESPACES = {"skeleton", "aufmass", "bauclock", "home_automation", "legal_private"}
PRIVATE_ROOT_ALIASES = (
    "SKELETON_PRIVATE_MEMORY_ROOT",
    "SKELETON_RUNNER_MEMORY_DIR",
    "SKELETON_HERMES_RUNTIME_ROOT",
    "HERMES_RUNTIME_ROOT",
    "SKELETON_HERMES_WORKSPACE_ROOT",
    "HERMES_WORKSPACE_ROOT",
    "SKELETON_HERMES_ARTIFACTS_ROOT",
    "HERMES_ARTIFACTS_ROOT",
)


class FragmentedMemoryMigrationError(RuntimeError):
    """Raised when fragmented memory audit or migration fails closed."""


@dataclass(frozen=True)
class DurableFactCandidate:
    source_ref: str
    source_hash: str
    source_kind: str
    namespace: str
    fact_id: str
    value: Mapping[str, Any]
    privacy_class: str
    confidence: str
    observed_at: str | None
    supersedes: str | None = None
    correction_of: str | None = None

    @property
    def canonical_ref(self) -> str:
        return f"{self.namespace}:{self.fact_id}"

    @property
    def value_hash(self) -> str:
        return content_hash(self.value)

    @property
    def durable_payload_hash(self) -> str:
        return content_hash(self.value.get("value") if isinstance(self.value, Mapping) else self.value)

    @property
    def idempotency_key(self) -> str:
        return "fragment_" + content_hash(
            {
                "source_ref": self.source_ref,
                "source_hash": self.source_hash,
                "canonical_ref": self.canonical_ref,
                "value_hash": self.value_hash,
            }
        )[:48]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit fragmented Skeleton memory and migrate durable facts.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--private-root", type=Path, default=None)
    parser.add_argument("--include-private-roots", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approval-ref", default="issue-2067-operator-audit")
    parser.add_argument("--actor-ref", default="fragmented-memory-migrator")
    parser.add_argument("--reason-code", default="fragmented-memory-migration")
    parser.add_argument("--max-bytes", type=int, default=1_048_576)
    parser.add_argument("--report-json", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    receipt = run_fragmented_memory_migration(
        repo_root=args.repo_root,
        private_root=args.private_root,
        include_private_roots=args.include_private_roots,
        apply=args.apply,
        approval_ref=args.approval_ref,
        actor_ref=args.actor_ref,
        reason_code=args.reason_code,
        max_bytes=args.max_bytes,
    )
    public_report = render_public_report(receipt)
    if args.report_json is not None:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(public_report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(public_report, sort_keys=True))
    return 0


def run_fragmented_memory_migration(
    *,
    repo_root: Path,
    private_root: Path | None = None,
    include_private_roots: bool = False,
    apply: bool = False,
    approval_ref: str = "issue-2067-operator-audit",
    actor_ref: str = "fragmented-memory-migrator",
    reason_code: str = "fragmented-memory-migration",
    max_bytes: int = 1_048_576,
) -> dict[str, object]:
    repo_root = repo_root.resolve()
    root = private_root or Path(os.environ.get(PRIVATE_MEMORY_STACK_ROOT_ENV, "~/.local/share/skeleton-private-memory")).expanduser()
    source_roots = [repo_root]
    if include_private_roots:
        source_roots.extend(_configured_private_roots(root))
    inventory, candidates = scan_fragmented_memory_sources(source_roots, repo_root=repo_root, max_bytes=max_bytes)
    plan = build_migration_plan(candidates)
    stack = PrivateMemoryStack(root)
    stack.init(import_manifest=True)
    before_status = stack.status()
    before_revision = int(before_status["canonical_sqlite"]["canonical_revision"])
    ledger_path = root / "fragmented_memory_migration_ledger.sqlite"
    backup = _backup_for_migration(stack, ledger_path=ledger_path) if apply and plan["accepted_count"] else None
    apply_receipt = (
        apply_migration_plan(
            plan,
            stack=stack,
            ledger_path=ledger_path,
            expected_revision=before_revision,
            actor_ref=actor_ref,
            reason_code=reason_code,
            approval_ref=approval_ref,
        )
        if apply
        else None
    )
    after_status = stack.status()
    integrity = _canonical_integrity(stack)
    return {
        "schema": APPLY_RECEIPT_SCHEMA if apply else PLAN_SCHEMA,
        "mode": "apply" if apply else "dry_run",
        "inventory": _aggregate_inventory(inventory),
        "plan": _public_plan(plan),
        "backup": _public_backup(backup),
        "apply": apply_receipt,
        "canonical_revision_before": before_revision,
        "canonical_revision_after": int(after_status["canonical_sqlite"]["canonical_revision"]),
        "canonical_integrity": integrity,
        "public_report_sha256": content_hash(
            {
                "inventory": _aggregate_inventory(inventory),
                "plan": _public_plan(plan),
                "mode": "apply" if apply else "dry_run",
            }
        ),
    }


def scan_fragmented_memory_sources(
    roots: Iterable[Path],
    *,
    repo_root: Path,
    max_bytes: int = 1_048_576,
) -> tuple[list[dict[str, object]], list[DurableFactCandidate]]:
    inventory: list[dict[str, object]] = []
    candidates: list[DurableFactCandidate] = []
    seen_source_hashes: set[str] = set()
    for root in roots:
        for path in _iter_source_paths(root):
            source_ref = _opaque_source_ref(path, repo_root)
            try:
                stat = path.stat()
            except OSError:
                inventory.append(_inventory_record(source_ref, "NEEDS_OPERATOR", "stat_failed"))
                continue
            source_hash = _source_hash(path, max_bytes=max_bytes)
            classification, reason = classify_source(path, source_hash=source_hash, size=stat.st_size)
            if source_hash in seen_source_hashes and classification == "MIGRATE_DURABLE_FACTS":
                classification, reason = "DUPLICATE", "duplicate_source_hash"
            seen_source_hashes.add(source_hash)
            extracted: list[DurableFactCandidate] = []
            if classification == "MIGRATE_DURABLE_FACTS":
                extracted, extraction_reason = extract_durable_facts(
                    path,
                    source_ref=source_ref,
                    source_hash=source_hash,
                    max_bytes=max_bytes,
                )
                if extraction_reason is not None:
                    classification, reason = extraction_reason, "extraction_not_migratable"
                else:
                    candidates.extend(extracted)
            inventory.append(
                _inventory_record(
                    source_ref,
                    classification,
                    reason,
                    source_hash=source_hash,
                    size=stat.st_size,
                    fact_count=len(extracted),
                )
            )
    return inventory, candidates


def classify_source(path: Path, *, source_hash: str, size: int) -> tuple[str, str]:
    name = path.name
    suffix = path.suffix.lower()
    if SECRET_RE.search(name):
        return "SECRET_OR_RESTRICTED", "restricted_name"
    if suffix in RAW_ARTIFACT_SUFFIXES:
        return "KEEP_AS_ARTIFACT", "raw_or_high_volume_artifact"
    if suffix in SQLITE_SUFFIXES:
        if CANONICAL_RE.search(str(path)):
            return "CANONICAL_ALREADY", "canonical_sqlite"
        return "NEEDS_OPERATOR", "sqlite_requires_adapter_review"
    if DERIVED_RE.search(str(path)):
        return "DERIVED", "derived_index_or_projection"
    if STALE_RE.search(str(path)):
        return "STALE", "stale_or_legacy_location"
    if OPERATIONAL_RE.search(str(path)) and suffix in {".yaml", ".yml", ".json", ".jsonl"}:
        return "OPERATIONAL_STATE_ONLY", "runtime_state"
    if suffix in STRUCTURED_SUFFIXES:
        return "MIGRATE_DURABLE_FACTS", "structured_source_candidate"
    if suffix in TEXT_SUFFIXES:
        return "NEEDS_OPERATOR", "unstructured_text_requires_operator_review"
    return "KEEP_AS_ARTIFACT", "unsupported_or_non_memory_artifact"


def extract_durable_facts(
    path: Path,
    *,
    source_ref: str,
    source_hash: str,
    max_bytes: int,
) -> tuple[list[DurableFactCandidate], str | None]:
    try:
        raw = path.read_bytes()[: max_bytes + 1]
    except OSError:
        return [], "NEEDS_OPERATOR"
    if len(raw) > max_bytes:
        return [], "KEEP_AS_ARTIFACT"
    text = raw.decode("utf-8", errors="replace")
    if SECRET_RE.search(text):
        return [], "SECRET_OR_RESTRICTED"
    try:
        payloads = _load_structured_payloads(path, text)
    except Exception:
        return [], "NEEDS_OPERATOR"
    facts: list[DurableFactCandidate] = []
    for payload in payloads:
        facts.extend(_extract_facts_from_payload(payload, source_ref=source_ref, source_hash=source_hash, source_kind=path.suffix.lower().lstrip(".") or "text"))
    if not facts:
        return [], "OPERATIONAL_STATE_ONLY"
    return facts, None


def build_migration_plan(candidates: Iterable[DurableFactCandidate]) -> dict[str, object]:
    by_ref: dict[str, DurableFactCandidate] = {}
    duplicate_count = 0
    conflict_count = 0
    needs_operator_count = 0
    accepted: list[DurableFactCandidate] = []
    for candidate in candidates:
        if candidate.namespace not in SUPPORTED_NAMESPACES:
            needs_operator_count += 1
            continue
        existing = by_ref.get(candidate.canonical_ref)
        if existing is None:
            by_ref[candidate.canonical_ref] = candidate
            accepted.append(candidate)
            continue
        if existing.durable_payload_hash == candidate.durable_payload_hash:
            duplicate_count += 1
            continue
        winner = _choose_supersession_winner(existing, candidate)
        if winner is None:
            conflict_count += 1
            accepted = [item for item in accepted if item.canonical_ref != candidate.canonical_ref]
            by_ref.pop(candidate.canonical_ref, None)
            continue
        loser = candidate if winner is existing else existing
        accepted = [item for item in accepted if item is not loser]
        if winner not in accepted:
            accepted.append(winner)
        by_ref[winner.canonical_ref] = winner
    accepted = sorted(accepted, key=lambda item: (item.namespace, item.fact_id, item.idempotency_key))
    return {
        "schema": PLAN_SCHEMA,
        "accepted": accepted,
        "accepted_count": len(accepted),
        "duplicate_count": duplicate_count,
        "conflict_count": conflict_count,
        "needs_operator_count": needs_operator_count,
        "plan_hash": content_hash([_candidate_fingerprint(item) for item in accepted]),
    }


def apply_migration_plan(
    plan: Mapping[str, object],
    *,
    stack: PrivateMemoryStack,
    ledger_path: Path,
    expected_revision: int,
    actor_ref: str,
    reason_code: str,
    approval_ref: str,
) -> dict[str, object]:
    _ensure_ledger(ledger_path)
    gateway = MemoryGateway(
        capability_token(namespaces=("skeleton",), public_mode=False),
        private_memory_storage=PrivateMemoryGatewayStorage(stack),
    )
    current_revision_hint = expected_revision
    migrated = 0
    duplicate = 0
    skipped = 0
    degraded_indexes: set[str] = set()
    receipts: list[dict[str, object]] = []
    for candidate in plan.get("accepted", []):
        if not isinstance(candidate, DurableFactCandidate):
            continue
        ledger = _ledger_lookup(ledger_path, candidate.idempotency_key)
        if ledger is not None and ledger["candidate_hash"] == content_hash(_candidate_fingerprint(candidate)):
            skipped += 1
            continue
        mutation = _gateway_mutation(
            candidate,
            expected_revision=current_revision_hint,
            actor_ref=actor_ref,
            reason_code=reason_code,
            approval_ref=approval_ref,
        )
        try:
            response = gateway.execute(
                {
                    "schema": MEMORY_GATEWAY_REQUEST_SCHEMA,
                    "namespace": "skeleton",
                    "command": "skeleton.memory.private_mutate",
                    "payload": mutation,
                }
            )
        except MemoryGatewayPolicyError:
            raise
        payload = response["payload"]
        if payload.get("idempotency_classification") == "DUPLICATE_IDENTICAL":
            duplicate += 1
        else:
            migrated += 1
        if isinstance(payload.get("canonical_revision"), int):
            current_revision_hint = int(payload["canonical_revision"])
        for name in payload.get("degraded_indexes", []) if isinstance(payload.get("degraded_indexes"), list) else []:
            degraded_indexes.add(str(name))
        _ledger_record(ledger_path, candidate, payload)
        receipts.append(_receipt_fingerprint(payload))
    return {
        "schema": APPLY_RECEIPT_SCHEMA,
        "status": "DONE",
        "migrated_count": migrated,
        "duplicate_count": duplicate,
        "ledger_skipped_count": skipped,
        "canonical_revision_last": current_revision_hint,
        "degraded_indexes": sorted(degraded_indexes),
        "receipt_hash": content_hash(receipts),
    }


def render_public_report(receipt: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema": REPORT_TEMPLATE_SCHEMA,
        "mode": receipt.get("mode"),
        "inventory": receipt.get("inventory"),
        "plan": receipt.get("plan"),
        "backup": receipt.get("backup"),
        "apply": receipt.get("apply"),
        "canonical_revision_before": receipt.get("canonical_revision_before"),
        "canonical_revision_after": receipt.get("canonical_revision_after"),
        "canonical_integrity": receipt.get("canonical_integrity"),
        "validation_notes": [
            "inventory_is_local_private_exact_public_aggregate_only",
            "durable_facts_only_gateway_mutations",
            "raw_documents_secrets_telemetry_excluded",
            "conflicts_fail_closed_or_require_operator",
        ],
        "public_report_sha256": receipt.get("public_report_sha256"),
    }


def _iter_source_paths(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.name in GENERATED_PRIVATE_MEMORY_NAMES or "backups" in path.parts:
            continue
        parts = set(path.parts)
        if parts & {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".venv", "venv"}:
            continue
        if path.suffix.lower() in TEXT_SUFFIXES | SQLITE_SUFFIXES | RAW_ARTIFACT_SUFFIXES:
            yield path


def _source_hash(path: Path, *, max_bytes: int) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            remaining = max_bytes
            while remaining > 0:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    break
                digest.update(chunk)
                remaining -= len(chunk)
    except OSError:
        digest.update(b"unreadable")
    return digest.hexdigest()


def _opaque_source_ref(path: Path, repo_root: Path) -> str:
    try:
        relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        relative = f"private:{content_hash(str(path))[:24]}"
    return "src_" + content_hash(relative)[:24]


def _inventory_record(
    source_ref: str,
    classification: str,
    reason: str,
    *,
    source_hash: str | None = None,
    size: int | None = None,
    fact_count: int = 0,
) -> dict[str, object]:
    if classification not in CLASSIFICATIONS:
        classification = "NEEDS_OPERATOR"
    return {
        "schema": AUDIT_SCHEMA,
        "source_ref": source_ref,
        "classification": classification,
        "reason_code": safe_token(reason, "reason_code"),
        "source_hash": source_hash,
        "size_bucket": _size_bucket(size),
        "fact_count": fact_count,
    }


def _size_bucket(size: int | None) -> str:
    if size is None:
        return "unknown"
    if size <= 4096:
        return "small"
    if size <= 1_048_576:
        return "medium"
    return "large"


def _load_structured_payloads(path: Path, text: str) -> list[Any]:
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return [yaml.safe_load(text)]
    if suffix == ".json":
        return [json.loads(text)]
    if suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if suffix == ".csv":
        return list(csv.DictReader(text.splitlines()))
    return []


def _extract_facts_from_payload(
    payload: Any,
    *,
    source_ref: str,
    source_hash: str,
    source_kind: str,
) -> list[DurableFactCandidate]:
    facts: list[DurableFactCandidate] = []
    if isinstance(payload, Mapping):
        explicit = payload.get("durable_memory_facts")
        if isinstance(explicit, list):
            for item in explicit:
                candidate = _candidate_from_mapping(item, source_ref=source_ref, source_hash=source_hash, source_kind=source_kind)
                if candidate is not None:
                    facts.append(candidate)
        if payload.get("schema") == "skeleton.fragmented_memory_fact.v1":
            candidate = _candidate_from_mapping(payload, source_ref=source_ref, source_hash=source_hash, source_kind=source_kind)
            if candidate is not None:
                facts.append(candidate)
    return facts


def _candidate_from_mapping(
    item: Any,
    *,
    source_ref: str,
    source_hash: str,
    source_kind: str,
) -> DurableFactCandidate | None:
    if not isinstance(item, Mapping):
        return None
    namespace = str(item.get("namespace", ""))
    fact_id = str(item.get("fact_id", ""))
    value = item.get("value")
    if namespace not in SUPPORTED_NAMESPACES or not fact_id or not isinstance(value, Mapping):
        return None
    if item.get("durability") not in {None, "durable"}:
        return None
    privacy_class = safe_token(str(item.get("privacy_class", "private")), "privacy_class")
    if privacy_class in {"secret", "credential", "raw_document", "raw_telemetry"}:
        return None
    confidence = safe_token(str(item.get("confidence", "medium")), "confidence")
    return DurableFactCandidate(
        source_ref=source_ref,
        source_hash=source_hash,
        source_kind=source_kind,
        namespace=safe_token(namespace, "namespace"),
        fact_id=safe_token(fact_id, "fact_id"),
        value={
            "schema": "skeleton.migrated_durable_fact.v1",
            "value": dict(value),
            "provenance": {
                "source_ref": source_ref,
                "source_hash": source_hash,
                "source_kind": source_kind,
                "privacy_class": privacy_class,
                "confidence": confidence,
                "observed_at": item.get("observed_at") if isinstance(item.get("observed_at"), str) else None,
                "supersedes": item.get("supersedes") if isinstance(item.get("supersedes"), str) else None,
                "correction_of": item.get("correction_of") if isinstance(item.get("correction_of"), str) else None,
            },
        },
        privacy_class=privacy_class,
        confidence=confidence,
        observed_at=item.get("observed_at") if isinstance(item.get("observed_at"), str) else None,
        supersedes=item.get("supersedes") if isinstance(item.get("supersedes"), str) else None,
        correction_of=item.get("correction_of") if isinstance(item.get("correction_of"), str) else None,
    )


def _choose_supersession_winner(
    left: DurableFactCandidate,
    right: DurableFactCandidate,
) -> DurableFactCandidate | None:
    if right.correction_of == left.source_ref or right.supersedes == left.source_ref:
        return right
    if left.correction_of == right.source_ref or left.supersedes == right.source_ref:
        return left
    return None


def _gateway_mutation(
    candidate: DurableFactCandidate,
    *,
    expected_revision: int,
    actor_ref: str,
    reason_code: str,
    approval_ref: str,
) -> dict[str, object]:
    return {
        "schema": PRIVATE_MEMORY_GATEWAY_MUTATION_SCHEMA,
        "project_id": "skeleton",
        "dataset_id": "fragmented_memory_migration",
        "operation": "put",
        "fact_namespace": candidate.namespace,
        "fact_id": candidate.fact_id,
        "value": candidate.value,
        "source_hash": candidate.source_hash,
        "actor_ref": actor_ref,
        "reason_code": reason_code,
        "approval_ref": approval_ref,
        "expected_revision": expected_revision,
        "idempotency_key": candidate.idempotency_key,
    }


def _ensure_ledger(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(path))) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS fragmented_memory_migration_ledger (
                idempotency_key TEXT PRIMARY KEY,
                schema TEXT NOT NULL,
                candidate_hash TEXT NOT NULL,
                canonical_ref TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                value_hash TEXT NOT NULL,
                canonical_revision INTEGER NOT NULL,
                receipt_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        connection.commit()
    path.chmod(0o600)


def _ledger_lookup(path: Path, idempotency_key: str) -> dict[str, object] | None:
    _ensure_ledger(path)
    with closing(sqlite3.connect(str(path))) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT idempotency_key, candidate_hash, canonical_ref, canonical_revision
            FROM fragmented_memory_migration_ledger
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
    return dict(row) if row is not None else None


def _ledger_record(path: Path, candidate: DurableFactCandidate, receipt: Mapping[str, object]) -> None:
    _ensure_ledger(path)
    with closing(sqlite3.connect(str(path))) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO fragmented_memory_migration_ledger (
                idempotency_key, schema, candidate_hash, canonical_ref, source_ref,
                source_hash, value_hash, canonical_revision, receipt_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.idempotency_key,
                LEDGER_SCHEMA,
                content_hash(_candidate_fingerprint(candidate)),
                candidate.canonical_ref,
                candidate.source_ref,
                candidate.source_hash,
                candidate.value_hash,
                int(receipt.get("canonical_revision", 0)),
                content_hash(_receipt_fingerprint(receipt)),
                int(time.time()),
            ),
        )
        connection.commit()


def _backup_for_migration(stack: PrivateMemoryStack, *, ledger_path: Path) -> dict[str, object]:
    backup = stack.backup(snapshot_id="fragmented-memory-migration")
    ledger_hash = _source_hash(ledger_path, max_bytes=16_777_216) if ledger_path.exists() else content_hash("ledger-empty")
    if backup.get("status") != "DONE":
        raise FragmentedMemoryMigrationError("canonical backup failed")
    return {**backup, "ledger_hash": ledger_hash}


def _canonical_integrity(stack: PrivateMemoryStack) -> dict[str, object]:
    db = stack.paths.db
    with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        report = sanitized_integrity_report(connection)
        report["canonical_revision"] = current_revision(connection)
    return {
        "status": report.get("status"),
        "integrity_ok": report.get("integrity_ok"),
        "canonical_revision": report.get("canonical_revision"),
        "fact_count": report.get("fact_count"),
        "event_count": report.get("event_count"),
        "tombstone_count": report.get("tombstone_count"),
    }


def _configured_private_roots(primary: Path) -> list[Path]:
    roots = [primary]
    for name in PRIVATE_ROOT_ALIASES:
        value = os.environ.get(name)
        if value:
            roots.append(Path(value).expanduser())
    result: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            key = str(root.resolve())
        except OSError:
            key = str(root)
        if key not in seen:
            seen.add(key)
            result.append(root)
    return result


def _aggregate_inventory(inventory: Iterable[Mapping[str, object]]) -> dict[str, object]:
    counts = Counter(str(item.get("classification", "NEEDS_OPERATOR")) for item in inventory)
    return {
        "schema": AUDIT_SCHEMA,
        "source_count": sum(counts.values()),
        "classification_counts": {classification: counts.get(classification, 0) for classification in CLASSIFICATIONS},
        "inventory_hash": content_hash(
            [
                {
                    "source_ref": item.get("source_ref"),
                    "classification": item.get("classification"),
                    "source_hash": item.get("source_hash"),
                    "fact_count": item.get("fact_count", 0),
                }
                for item in inventory
            ]
        ),
    }


def _public_plan(plan: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema": PLAN_SCHEMA,
        "accepted_count": plan.get("accepted_count", 0),
        "duplicate_count": plan.get("duplicate_count", 0),
        "conflict_count": plan.get("conflict_count", 0),
        "needs_operator_count": plan.get("needs_operator_count", 0),
        "plan_hash": plan.get("plan_hash"),
    }


def _public_backup(backup: Mapping[str, object] | None) -> dict[str, object]:
    if backup is None:
        return {"status": "NOT_REQUIRED"}
    return {
        "status": backup.get("status"),
        "snapshot_confirmed": backup.get("status") == "DONE",
        "canonical_revision": backup.get("canonical_revision"),
        "aggregate_counts": backup.get("aggregate_counts"),
        "ledger_hash": backup.get("ledger_hash"),
    }


def _candidate_fingerprint(candidate: DurableFactCandidate) -> dict[str, object]:
    return {
        "source_ref": candidate.source_ref,
        "source_hash": candidate.source_hash,
        "canonical_ref": candidate.canonical_ref,
        "value_hash": candidate.value_hash,
        "durable_payload_hash": candidate.durable_payload_hash,
        "privacy_class": candidate.privacy_class,
        "confidence": candidate.confidence,
    }


def _receipt_fingerprint(receipt: Mapping[str, object]) -> dict[str, object]:
    return {
        "status": receipt.get("status"),
        "operation": receipt.get("operation"),
        "canonical_ref": receipt.get("canonical_ref"),
        "canonical_revision": receipt.get("canonical_revision"),
        "source_hash": receipt.get("source_hash"),
        "idempotency_classification": receipt.get("idempotency_classification"),
        "degraded_indexes": receipt.get("degraded_indexes", []),
    }


if __name__ == "__main__":
    raise SystemExit(main())
