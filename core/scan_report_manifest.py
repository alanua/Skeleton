from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from core.local_inference_adapters import validate_json_schema


MANIFEST_SCHEMA_ID = "skeleton.scan_report_manifest.v1"
REPORT_VERSION = 1
LOW_CONFIDENCE_THRESHOLD = 0.80
LOW_OCR_QUALITY_THRESHOLD = 0.70
TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_TIMEOUT_SECONDS = 10
TELEGRAM_DELIVERY_MAX_ATTEMPTS = 3

OVERALL_STATUSES = (
    "success",
    "partial_success",
    "review_required",
    "error",
    "awaiting_additional_pass",
)
PROCESSING_STATUSES = (
    "completed",
    "review_required",
    "failed",
    "awaiting_additional_pass",
)
DELIVERY_STATUSES = ("prepared", "sent", "delivered", "failed", "superseded")
REPORT_ACTIONS = (
    "download_original",
    "download_searchable_pdf",
    "confirm_classification",
    "change_owner",
    "split_document",
    "merge_previous",
    "merge_next",
    "open_review_item",
)
DELIVERY_RECEIPT_SCHEMA_ID = "skeleton.scan_report_delivery_receipt.v1"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")


SCAN_REPORT_MANIFEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema",
        "report_version",
        "manifest_id",
        "manifest_hash",
        "session_id",
        "scan_id",
        "created_at",
        "overall_status",
        "package_summary",
        "documents",
        "delivery",
        "audit",
    ],
    "additionalProperties": False,
    "properties": {
        "schema": {"const": MANIFEST_SCHEMA_ID},
        "report_version": {"type": "integer", "const": REPORT_VERSION},
        "manifest_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "manifest_hash": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "session_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "scan_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "created_at": {"type": "string", "minLength": 20, "maxLength": 40},
        "overall_status": {"enum": list(OVERALL_STATUSES)},
        "package_summary": {
            "type": "object",
            "required": [
                "package_id",
                "physical_page_count",
                "logical_document_count",
                "completed_count",
                "review_required_count",
                "failed_count",
                "overall_status",
            ],
            "additionalProperties": False,
            "properties": {
                "package_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "physical_page_count": {"type": "integer", "minimum": 0, "maximum": 5000},
                "logical_document_count": {"type": "integer", "minimum": 0, "maximum": 1000},
                "completed_count": {"type": "integer", "minimum": 0, "maximum": 1000},
                "review_required_count": {"type": "integer", "minimum": 0, "maximum": 1000},
                "failed_count": {"type": "integer", "minimum": 0, "maximum": 1000},
                "overall_status": {"enum": list(OVERALL_STATUSES)},
            },
        },
        "documents": {"type": "array", "items": {"type": "object"}},
        "delivery": {"type": "object"},
        "audit": {"type": "object"},
    },
}


class ScanReportError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedArtifact:
    artifact_id: str
    kind: str
    canonical_path: str
    page_count: int
    sha256: str
    verified: bool


@dataclass(frozen=True)
class LinkMetadata:
    url: str
    expires_at: str
    provider: str
    artifact_id: str


class PrivateDownloadLinkProvider:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        secret: str | None = None,
        ttl_seconds: int = 3600,
    ) -> None:
        self.base_url = (base_url or os.environ.get("SKELETON_PRIVATE_DOWNLOAD_BASE_URL", "")).rstrip("/")
        self.secret = secret or os.environ.get("SKELETON_PRIVATE_DOWNLOAD_LINK_SECRET", "")
        self.ttl_seconds = ttl_seconds
        if not self.base_url:
            raise ScanReportError("private_download_base_url_missing")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ScanReportError("private_download_base_url_invalid")
        if not self.secret:
            raise ScanReportError("private_download_secret_missing")

    def link_for(self, artifact: VerifiedArtifact, *, now: int | None = None) -> LinkMetadata:
        current = int(time.time()) if now is None else now
        expires = current + self.ttl_seconds
        token_input = f"{artifact.artifact_id}.{expires}.{artifact.sha256}".encode("utf-8")
        signature = hmac.new(self.secret.encode("utf-8"), token_input, hashlib.sha256).hexdigest()
        query = urllib.parse.urlencode(
            {"artifact_id": artifact.artifact_id, "expires": str(expires), "sig": signature}
        )
        return LinkMetadata(
            url=f"{self.base_url}/download?{query}",
            expires_at=_utc_from_timestamp(expires),
            provider="skeleton_private_download.v1",
            artifact_id=artifact.artifact_id,
        )


class ScanReportDeliveryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_report_delivery (
                idempotency_key TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                report_version INTEGER NOT NULL,
                manifest_hash TEXT NOT NULL,
                delivery_status TEXT NOT NULL,
                message_ids_json TEXT NOT NULL,
                attempt_count INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                error_reason TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_report_audit (
                audit_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                delivery_status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                message_ids_json TEXT NOT NULL,
                error_reason TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_report_retry_queue (
                idempotency_key TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                next_attempt_at REAL NOT NULL,
                attempt_count INTEGER NOT NULL,
                last_error_reason TEXT NOT NULL
            )
            """
        )
        connection.commit()

    def read(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM scan_report_delivery WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["message_ids"] = json.loads(value.pop("message_ids_json"))
        return value

    def read_current(self, session_id: str, report_version: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM scan_report_delivery
                WHERE session_id = ?
                  AND report_version = ?
                  AND delivery_status IN ('sent', 'delivered')
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (session_id, report_version),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["message_ids"] = json.loads(value.pop("message_ids_json"))
        return value

    def persist(
        self,
        *,
        idempotency_key: str,
        manifest: Mapping[str, Any],
        delivery_status: str,
        message_ids: Sequence[int],
        attempt_count: int,
        error_reason: str | None = None,
    ) -> None:
        _validate_delivery_status(delivery_status)
        now = _utc_now()
        session_id = str(manifest["session_id"])
        report_version = int(manifest["report_version"])
        manifest_hash = str(manifest["manifest_hash"])
        manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        message_ids_json = json.dumps(list(message_ids), sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scan_report_delivery
                (idempotency_key, session_id, report_version, manifest_hash, delivery_status,
                 message_ids_json, attempt_count, updated_at, error_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    manifest_hash = excluded.manifest_hash,
                    delivery_status = excluded.delivery_status,
                    message_ids_json = excluded.message_ids_json,
                    attempt_count = excluded.attempt_count,
                    updated_at = excluded.updated_at,
                    error_reason = excluded.error_reason
                """,
                (
                    idempotency_key,
                    session_id,
                    report_version,
                    manifest_hash,
                    delivery_status,
                    message_ids_json,
                    attempt_count,
                    now,
                    _safe_reason(error_reason) if error_reason else None,
                ),
            )
            connection.execute(
                """
                INSERT INTO scan_report_audit
                (audit_id, idempotency_key, manifest_hash, delivery_status, created_at,
                 manifest_json, message_ids_json, error_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    idempotency_key,
                    manifest_hash,
                    delivery_status,
                    now,
                    manifest_json,
                    message_ids_json,
                    _safe_reason(error_reason) if error_reason else None,
                ),
            )
            if delivery_status == "failed":
                connection.execute(
                    """
                    INSERT INTO scan_report_retry_queue
                    (idempotency_key, session_id, manifest_hash, next_attempt_at,
                     attempt_count, last_error_reason)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(idempotency_key) DO UPDATE SET
                        manifest_hash = excluded.manifest_hash,
                        next_attempt_at = excluded.next_attempt_at,
                        attempt_count = excluded.attempt_count,
                        last_error_reason = excluded.last_error_reason
                    """,
                    (
                        idempotency_key,
                        session_id,
                        manifest_hash,
                        time.time() + min(300, 2 ** max(0, attempt_count - 1)),
                        attempt_count,
                        _safe_reason(error_reason or "telegram_delivery_failed"),
                    ),
                )
            else:
                connection.execute(
                    "DELETE FROM scan_report_retry_queue WHERE idempotency_key = ?",
                    (idempotency_key,),
                )
            connection.commit()

    def supersede(self, idempotency_key: str) -> None:
        current = self.read(idempotency_key)
        if current is None:
            return
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT manifest_hash, manifest_json, message_ids_json
                FROM scan_report_audit
                WHERE idempotency_key = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (idempotency_key,),
            ).fetchone()
            connection.execute(
                "UPDATE scan_report_delivery SET delivery_status = ?, updated_at = ? WHERE idempotency_key = ?",
                ("superseded", _utc_now(), idempotency_key),
            )
            if row is not None:
                connection.execute(
                    """
                    INSERT INTO scan_report_audit
                    (audit_id, idempotency_key, manifest_hash, delivery_status, created_at,
                     manifest_json, message_ids_json, error_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        idempotency_key,
                        row["manifest_hash"],
                        "superseded",
                        _utc_now(),
                        row["manifest_json"],
                        row["message_ids_json"],
                        None,
                    ),
                )
            connection.commit()

    def audit_count(self, idempotency_key: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM scan_report_audit
                WHERE idempotency_key = ?
                   OR idempotency_key LIKE ?
                """,
                (idempotency_key, f"{idempotency_key}:%"),
            ).fetchone()
        return int(row["count"])

    def retry_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM scan_report_retry_queue",
            ).fetchone()
        return int(row["count"])


def build_scan_report_manifest(
    package: Mapping[str, Any],
    *,
    link_provider: PrivateDownloadLinkProvider,
    created_at: str | None = None,
) -> dict[str, Any]:
    session_id = _safe_id(package.get("session_id"), "session_id")
    scan_id = _safe_id(package.get("scan_id", session_id), "scan_id")
    package_id = _safe_id(package.get("package_id", session_id), "package_id")
    physical_page_count = _physical_page_count(package)
    documents_raw = package.get("documents")
    if not isinstance(documents_raw, Sequence) or isinstance(documents_raw, (str, bytes)):
        raise ScanReportError("documents_invalid")

    documents: list[dict[str, Any]] = []
    seen_pages: list[int] = []
    failures: list[dict[str, str]] = []
    for raw in documents_raw:
        if not isinstance(raw, Mapping):
            raise ScanReportError("document_invalid")
        document, document_failures = _build_document_record(
            raw,
            session_id=session_id,
            physical_page_count=physical_page_count,
            link_provider=link_provider,
        )
        seen_pages.extend(document["pages"])
        failures.extend(document_failures)
        documents.append(document)

    _validate_page_mapping(seen_pages, physical_page_count)
    completed = sum(1 for document in documents if document["processing_status"] == "completed")
    review = sum(1 for document in documents if document["processing_status"] == "review_required")
    failed = sum(1 for document in documents if document["processing_status"] == "failed")
    overall_status = _overall_status(completed=completed, review=review, failed=failed, total=len(documents))
    now = created_at or _utc_now()
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA_ID,
        "report_version": REPORT_VERSION,
        "manifest_id": f"{session_id}.scan_report.v{REPORT_VERSION}",
        "manifest_hash": "0" * 64,
        "session_id": session_id,
        "scan_id": scan_id,
        "created_at": now,
        "overall_status": overall_status,
        "package_summary": {
            "package_id": package_id,
            "physical_page_count": physical_page_count,
            "logical_document_count": len(documents),
            "completed_count": completed,
            "review_required_count": review,
            "failed_count": failed,
            "overall_status": overall_status,
        },
        "documents": documents,
        "delivery": {
            "telegram": {
                "status": "prepared",
                "idempotency_scope": "session_id+manifest_version+manifest_sha",
                "message_ids": [],
                "attempt_count": 0,
            }
        },
        "audit": {
            "mandatory_stages": list(package.get("mandatory_stages", [])),
            "verification_status": "failed" if failures else "verified",
            "failures": failures,
            "supersedes": list(package.get("supersedes", [])),
        },
    }
    manifest["manifest_hash"] = _manifest_hash(manifest)
    validate_scan_report_manifest(manifest)
    return manifest


def validate_scan_report_manifest(manifest: Mapping[str, Any]) -> None:
    validate_json_schema(manifest, SCAN_REPORT_MANIFEST_SCHEMA)
    expected = _manifest_hash(manifest)
    if manifest.get("manifest_hash") != expected:
        raise ScanReportError("manifest_hash_mismatch")
    documents = manifest.get("documents")
    if not isinstance(documents, list):
        raise ScanReportError("documents_invalid")
    seen: list[int] = []
    for document in documents:
        if not isinstance(document, Mapping):
            raise ScanReportError("document_invalid")
        _validate_document_record(document)
        seen.extend(document["pages"])
    physical = manifest["package_summary"]["physical_page_count"]  # type: ignore[index]
    _validate_page_mapping(seen, int(physical))


def render_telegram_report(manifest: Mapping[str, Any]) -> list[tuple[str, dict[str, Any] | None]]:
    validate_scan_report_manifest(manifest)
    summary = manifest["package_summary"]
    assert isinstance(summary, Mapping)
    messages: list[tuple[str, dict[str, Any] | None]] = [
        (
            "\n".join(
                (
                    "Сканування завершено"
                    if summary["overall_status"] == "success"
                    else "Сканування partially completed",
                    f"Package: {summary['package_id']}",
                    f"Pages: {summary['physical_page_count']}",
                    f"Documents: {summary['logical_document_count']}",
                    (
                        f"Ready/review/error: {summary['completed_count']}/"
                        f"{summary['review_required_count']}/{summary['failed_count']}"
                    ),
                    f"Status: {summary['overall_status']}",
                )
            ),
            None,
        )
    ]
    for document in manifest["documents"]:
        assert isinstance(document, Mapping)
        messages.append((_render_document_card(document), _document_reply_markup(document)))
    return messages


def deliver_scan_report(
    manifest: Mapping[str, Any],
    *,
    store: ScanReportDeliveryStore,
    sender: Callable[[str, dict[str, Any] | None], int | None] | None = None,
) -> dict[str, Any]:
    validate_scan_report_manifest(manifest)
    session_id = str(manifest["session_id"])
    report_version = int(manifest["report_version"])
    idempotency_key = report_idempotency_key(session_id, report_version, str(manifest["manifest_hash"]))
    existing = store.read(idempotency_key)
    if existing and existing["manifest_hash"] == manifest["manifest_hash"] and existing["delivery_status"] in {"sent", "delivered"}:
        return _delivery_receipt(
            manifest,
            status="delivered",
            idempotency="duplicate_replay",
            message_ids=existing["message_ids"],
            attempt_count=int(existing["attempt_count"]),
        )
    prior = store.read_current(session_id, report_version)
    superseded_prior = False
    if prior and prior["idempotency_key"] != idempotency_key and prior["manifest_hash"] != manifest["manifest_hash"]:
        store.supersede(str(prior["idempotency_key"]))
        superseded_prior = True

    send = sender or _send_telegram_message
    message_ids: list[int] = []
    attempt_count = int(existing["attempt_count"]) + 1 if existing else 1
    try:
        for text, reply_markup in render_telegram_report(manifest):
            message_id = send(text, reply_markup)
            if message_id is not None:
                message_ids.append(int(message_id))
    except Exception as exc:
        reason = _public_error_reason(exc)
        status = "failed"
        if attempt_count >= TELEGRAM_DELIVERY_MAX_ATTEMPTS:
            reason = f"dead_letter:{reason}"[:96]
        store.persist(
            idempotency_key=idempotency_key,
            manifest=manifest,
            delivery_status=status,
            message_ids=message_ids,
            attempt_count=attempt_count,
            error_reason=reason,
        )
        return _delivery_receipt(
            manifest,
            status="failed",
            idempotency="new_or_retry",
            message_ids=message_ids,
            attempt_count=attempt_count,
            reason=reason,
        )

    store.persist(
        idempotency_key=idempotency_key,
        manifest=manifest,
        delivery_status="delivered",
        message_ids=message_ids,
        attempt_count=attempt_count,
    )
    return _delivery_receipt(
        manifest,
        status="delivered",
        idempotency="superseded" if superseded_prior else "new",
        message_ids=message_ids,
        attempt_count=attempt_count,
    )


def _delivery_receipt(
    manifest: Mapping[str, Any],
    *,
    status: str,
    idempotency: str,
    message_ids: Sequence[int],
    attempt_count: int,
    reason: str | None = None,
) -> dict[str, Any]:
    session_id = str(manifest["session_id"])
    report_version = int(manifest["report_version"])
    manifest_hash = str(manifest["manifest_hash"])
    artifact_hashes = _artifact_hash_receipts(manifest)
    idempotency_key = report_idempotency_key(session_id, report_version, manifest_hash)
    receipt_seed = json.dumps(
        {
            "idempotency_key": idempotency_key,
            "manifest_hash": manifest_hash,
            "message_ids": list(message_ids),
            "status": status,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    receipt = {
        "schema": DELIVERY_RECEIPT_SCHEMA_ID,
        "delivery_receipt_id": f"scan-report-receipt:{hashlib.sha256(receipt_seed.encode('utf-8')).hexdigest()[:24]}",
        "session_id": session_id,
        "manifest_id": str(manifest["manifest_id"]),
        "manifest_version": report_version,
        "manifest_sha256": manifest_hash,
        "channel": "telegram",
        "status": status,
        "idempotency": idempotency,
        "idempotency_key": idempotency_key,
        "message_ids": list(message_ids),
        "delivered_at": _utc_now() if status == "delivered" else None,
        "attempt_status": status,
        "attempt_count": attempt_count,
        "retry_state": _retry_state(status=status, attempt_count=attempt_count, reason=reason),
        "reason": _safe_reason(reason) if reason else None,
        "documents": _document_receipts(manifest),
        "artifact_sha256_values": artifact_hashes,
        "traceability": {
            "scan_id": _optional_text(manifest.get("scan_id")) or session_id,
            "batch_session_id": session_id,
            "manifest_id": str(manifest["manifest_id"]),
            "document_ids": [str(document["document_id"]) for document in manifest["documents"] if isinstance(document, Mapping)],
            "artifact_ids": sorted(artifact_hashes),
            "delivery_receipt_id": f"scan-report-receipt:{hashlib.sha256(receipt_seed.encode('utf-8')).hexdigest()[:24]}",
            "memory_gate_record_id": _optional_text(manifest.get("memory_gate_record_id")),
        },
    }
    rendered = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    if re.search(r"(?i)(/home/|/tmp/|file:|https?://|token|bearer|cookie|password|secret)", rendered):
        raise ScanReportError("delivery_receipt_private_leak")
    return receipt


def _document_receipts(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for document in manifest["documents"]:
        if not isinstance(document, Mapping):
            continue
        artifacts = document.get("artifacts")
        artifact_ids = sorted(
            str(artifact["artifact_id"])
            for artifact in artifacts.values()
            if isinstance(artifacts, Mapping)
            if isinstance(artifact, Mapping) and artifact.get("artifact_id")
        )
        receipts.append(
            {
                "document_id": str(document["document_id"]),
                "owner_id": _stable_public_id(document.get("recipient_owner"), prefix="owner"),
                "processing_status": str(document["processing_status"]),
                "review_required": bool(document["review_required"]),
                "page_count": int(document["page_count"]),
                "page_range": str(document["page_range"]),
                "artifact_ids": artifact_ids,
                "idempotency_key": _document_idempotency_key(
                    str(manifest["session_id"]),
                    str(document["document_id"]),
                    int(manifest["report_version"]),
                    str(manifest["manifest_hash"]),
                ),
            }
        )
    return receipts


def _artifact_hash_receipts(manifest: Mapping[str, Any]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for document in manifest["documents"]:
        if not isinstance(document, Mapping):
            continue
        artifacts = document.get("artifacts")
        if not isinstance(artifacts, Mapping):
            continue
        for artifact in artifacts.values():
            if not isinstance(artifact, Mapping):
                continue
            artifact_id = artifact.get("artifact_id")
            sha256 = artifact.get("sha256")
            if isinstance(artifact_id, str) and isinstance(sha256, str) and _HASH_RE.match(sha256):
                hashes[artifact_id] = sha256
    return dict(sorted(hashes.items()))


def _retry_state(*, status: str, attempt_count: int, reason: str | None) -> str:
    if status == "delivered":
        return "not_applicable"
    if reason and str(reason).startswith("dead_letter:"):
        return "dead_letter"
    if attempt_count >= TELEGRAM_DELIVERY_MAX_ATTEMPTS:
        return "dead_letter"
    return "queued_retry"


def _document_idempotency_key(session_id: str, document_id: str, report_version: int, manifest_sha: str) -> str:
    if not _HASH_RE.match(manifest_sha):
        raise ScanReportError("manifest_hash_invalid")
    _safe_id(document_id, "document_id")
    return f"scan-report:{session_id}:{document_id}:v{report_version}:{manifest_sha[:16]}"


def _stable_public_id(value: object, *, prefix: str) -> str | None:
    text = _optional_text(value)
    if not text:
        return None
    return f"{prefix}:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"


def write_scan_report_manifest(manifest: Mapping[str, Any], path: str | Path) -> None:
    validate_scan_report_manifest(manifest)
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        os.chmod(temporary, 0o600)
        json.dump(manifest, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def report_idempotency_key(session_id: str, report_version: int, manifest_sha: str | None = None) -> str:
    if manifest_sha is None:
        return f"scan-report:{session_id}:v{report_version}"
    if not _HASH_RE.match(manifest_sha):
        raise ScanReportError("manifest_hash_invalid")
    return f"scan-report:{session_id}:v{report_version}:{manifest_sha[:16]}"


def _build_document_record(
    raw: Mapping[str, Any],
    *,
    session_id: str,
    physical_page_count: int,
    link_provider: PrivateDownloadLinkProvider,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    document_id = _safe_id(raw.get("document_id"), "document_id")
    pages = _pages(raw.get("pages") or raw.get("page_list"))
    if not pages or min(pages) < 1 or max(pages) > physical_page_count:
        raise ScanReportError("document_pages_invalid")
    page_range = _page_range(pages)
    expected_page_count = len(pages)
    inference = raw.get("classification") or raw.get("inference") or {}
    if not isinstance(inference, Mapping):
        raise ScanReportError("classification_invalid")
    confidences = _confidence_values(inference)
    ocr_quality = _optional_float(raw.get("ocr_quality"))
    raw_summary = raw.get("summary") or inference.get("summary")
    missing_content_summary = not bool(_optional_text(raw_summary))
    unreliable = (
        str(inference.get("route", "")).upper() == "REVIEW"
        or confidences["overall"] < LOW_CONFIDENCE_THRESHOLD
        or (ocr_quality is not None and ocr_quality < LOW_OCR_QUALITY_THRESHOLD)
        or missing_content_summary
    )
    failures: list[dict[str, str]] = []
    artifacts: dict[str, dict[str, Any]] = {}
    for kind, raw_path in (
        ("original_stitched_pdf", raw.get("original_stitched_pdf")),
        ("searchable_pdf", raw.get("searchable_pdf")),
    ):
        try:
            artifact = verify_pdf_artifact(
                raw_path,
                expected_page_count=expected_page_count,
                artifact_id=f"{session_id}:{document_id}:{kind}",
                kind=kind,
            )
            link = link_provider.link_for(artifact)
            artifacts[kind] = {
                "artifact_id": artifact.artifact_id,
                "canonical_path": artifact.canonical_path,
                "page_count": artifact.page_count,
                "sha256": artifact.sha256,
                "verified": artifact.verified,
                "link": {
                    "url": link.url,
                    "expires_at": link.expires_at,
                    "provider": link.provider,
                    "artifact_id": link.artifact_id,
                },
            }
        except ScanReportError as exc:
            failures.append({"document_id": document_id, "stage": kind, "reason": str(exc)})
    processing_status = _processing_status(raw, unreliable=unreliable, artifact_failures=failures)
    topic = _optional_text(raw.get("topic") or inference.get("topic_alias"))
    owner = _optional_text(raw.get("recipient_owner") or inference.get("principal_subject_alias"))
    title = _optional_text(raw.get("title") or inference.get("document_type") or "Untitled document")
    sender = _optional_text(raw.get("sender") or inference.get("issuer"))
    summary = _summary(raw_summary, unreliable=unreliable)
    review_reason_codes = list(raw.get("review_reason_codes") or inference.get("reason_codes") or [])
    if unreliable and "LOW_CONFIDENCE_OR_OCR" not in review_reason_codes:
        review_reason_codes.append("LOW_CONFIDENCE_OR_OCR")
    if missing_content_summary and "MISSING_OCR_CONTENT_SUMMARY" not in review_reason_codes:
        review_reason_codes.append("MISSING_OCR_CONTENT_SUMMARY")
    return (
        {
            "document_id": document_id,
            "pages": pages,
            "page_range": page_range,
            "page_count": expected_page_count,
            "title": title,
            "sender": sender,
            "recipient_owner": owner,
            "document_date": _optional_text(raw.get("date") or inference.get("document_date")),
            "country": _optional_text(raw.get("country") or inference.get("jurisdiction_country")),
            "document_type": _optional_text(raw.get("document_type") or inference.get("document_type")),
            "topic": topic,
            "classification_path": _classification_path(owner, topic, raw.get("classification_path")),
            "confidence": confidences,
            "ocr_quality": ocr_quality,
            "processing_status": processing_status,
            "summary": summary,
            "summary_reliability": "unreliable" if unreliable else "reliable",
            "review_required": processing_status == "review_required",
            "review_reason_codes": [_safe_reason(str(code)) for code in review_reason_codes],
            "human_storage_path": _human_storage_path(raw.get("human_storage_path"), owner, topic, raw.get("date") or inference.get("document_date")),
            "artifacts": artifacts,
            "actions": _document_actions(document_id, artifacts, processing_status),
        },
        failures,
    )


def verify_pdf_artifact(
    path: object,
    *,
    expected_page_count: int,
    artifact_id: str,
    kind: str,
) -> VerifiedArtifact:
    if kind not in {"original_stitched_pdf", "searchable_pdf"}:
        raise ScanReportError("artifact_kind_invalid")
    if not isinstance(path, (str, Path)) or not str(path):
        raise ScanReportError("artifact_path_missing")
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ScanReportError("artifact_missing")
    data = resolved.read_bytes()
    if not data.startswith(b"%PDF"):
        raise ScanReportError("pdf_open_failed")
    page_count = _pdf_page_count(resolved, data)
    if page_count != expected_page_count:
        raise ScanReportError("pdf_page_count_mismatch")
    return VerifiedArtifact(
        artifact_id=artifact_id,
        kind=kind,
        canonical_path=str(resolved),
        page_count=page_count,
        sha256=hashlib.sha256(data).hexdigest(),
        verified=True,
    )


def _pdf_page_count(path: Path, data: bytes) -> int:
    try:
        from pypdf import PdfReader  # type: ignore

        return len(PdfReader(str(path)).pages)
    except Exception:
        text = data.decode("latin-1", errors="ignore")
        pages = re.findall(r"/Type\s*/Page(?!s)\b", text)
        if not pages:
            raise ScanReportError("pdf_open_failed")
        return len(pages)


def _send_telegram_message(message: str, reply_markup: dict[str, Any] | None) -> int | None:
    bot_token = os.environ.get("SKELETON_TG_BOT")
    chat_id = os.environ.get("SKELETON_TG_CHAT")
    if not bot_token or not chat_id:
        raise ScanReportError("telegram_secret_missing")
    fields = {"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}
    if reply_markup is not None:
        fields["reply_markup"] = json.dumps(reply_markup, sort_keys=True, separators=(",", ":"))
    request = urllib.request.Request(
        f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage",
        data=urllib.parse.urlencode(fields).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TELEGRAM_TIMEOUT_SECONDS) as response:
        body = response.read().decode("utf-8")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    result = payload.get("result") if isinstance(payload, Mapping) else None
    if isinstance(result, Mapping) and isinstance(result.get("message_id"), int):
        return int(result["message_id"])
    return None


def _render_document_card(document: Mapping[str, Any]) -> str:
    warning = "REVIEW REQUIRED: summary/classification unreliable" if document["review_required"] else "Status: completed"
    return "\n".join(
        (
            f"{document['title']}",
            f"Sender: {document['sender'] or 'unknown'}",
            f"Owner: {document['recipient_owner'] or 'unknown'}",
            f"Type: {document['document_type'] or 'unknown'}",
            f"Topic: {document['topic'] or 'unknown'}",
            f"Pages: {document['page_count']} ({document['page_range']})",
            f"Summary: {document['summary']}",
            f"Classification: {document['classification_path']} ({document['confidence']['overall']:.2f})",
            f"Storage: {document['human_storage_path']}",
            warning,
        )
    )


def _document_reply_markup(document: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    for action in document["actions"]:
        button: dict[str, Any] = {"text": action["label"]}
        if "url" in action:
            button["url"] = action["url"]
        else:
            button["callback_data"] = action["callback_data"]
        rows.append([button])
    return {"inline_keyboard": rows}


def _document_actions(document_id: str, artifacts: Mapping[str, Any], processing_status: str) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if "original_stitched_pdf" in artifacts:
        actions.append({"action": "download_original", "label": "Original", "url": artifacts["original_stitched_pdf"]["link"]["url"]})
    if "searchable_pdf" in artifacts:
        actions.append({"action": "download_searchable_pdf", "label": "Searchable PDF", "url": artifacts["searchable_pdf"]["link"]["url"]})
    callback_actions = ["confirm_classification", "change_owner", "split_document", "merge_previous", "merge_next"]
    if processing_status == "review_required":
        callback_actions.append("open_review_item")
    for action in callback_actions:
        actions.append(
            {
                "action": action,
                "label": action.replace("_", " ").title(),
                "callback_data": _callback_data(action, document_id),
            }
        )
    return actions


def _callback_data(action: str, document_id: str) -> str:
    if action not in REPORT_ACTIONS:
        raise ScanReportError("report_action_invalid")
    marker = hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:12]
    value = f"scan:v1:{action[:12]}:{marker}"
    if len(value.encode("utf-8")) > 64:
        raise ScanReportError("callback_data_too_long")
    return value


def _processing_status(raw: Mapping[str, Any], *, unreliable: bool, artifact_failures: Sequence[Mapping[str, str]]) -> str:
    explicit = raw.get("processing_status")
    if explicit is not None:
        status = str(explicit)
        if status not in PROCESSING_STATUSES:
            raise ScanReportError("processing_status_invalid")
        if status == "completed" and (unreliable or artifact_failures):
            return "review_required" if unreliable and not artifact_failures else "failed"
        return status
    if artifact_failures:
        return "failed"
    if unreliable:
        return "review_required"
    return "completed"


def _validate_document_record(document: Mapping[str, Any]) -> None:
    required = {
        "document_id", "pages", "page_range", "page_count", "title", "sender",
        "recipient_owner", "document_date", "country", "document_type", "topic",
        "classification_path", "confidence", "processing_status", "summary",
        "summary_reliability", "review_required", "human_storage_path", "artifacts",
        "actions",
    }
    if not required <= set(document):
        raise ScanReportError("document_record_missing_fields")
    _safe_id(document["document_id"], "document_id")
    pages = _pages(document["pages"])
    if int(document["page_count"]) != len(pages):
        raise ScanReportError("document_page_count_mismatch")
    if document["processing_status"] not in PROCESSING_STATUSES:
        raise ScanReportError("processing_status_invalid")
    confidence = document["confidence"]
    if not isinstance(confidence, Mapping) or not _HASH_RE.match("0" * 64):
        raise ScanReportError("confidence_invalid")
    for value in confidence.values():
        parsed = _optional_float(value)
        if parsed is None or not 0 <= parsed <= 1:
            raise ScanReportError("confidence_invalid")
    rendered = _render_document_card(document)
    if re.search(r"(?i)(/home/|/tmp/|file:|token|bearer|cookie|password|secret)", rendered):
        raise ScanReportError("telegram_text_private_leak")


def _validate_page_mapping(pages: Sequence[int], physical_page_count: int) -> None:
    if physical_page_count < 0:
        raise ScanReportError("physical_page_count_invalid")
    if len(set(pages)) != len(pages):
        raise ScanReportError("document_page_mapping_duplicate")
    if any(page < 1 or page > physical_page_count for page in pages):
        raise ScanReportError("document_page_mapping_out_of_range")


def _manifest_hash(manifest: Mapping[str, Any]) -> str:
    normalized = dict(manifest)
    normalized["manifest_hash"] = "0" * 64
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _confidence_values(inference: Mapping[str, Any]) -> dict[str, float]:
    raw = inference.get("confidence") if isinstance(inference.get("confidence"), Mapping) else {}
    assert isinstance(raw, Mapping)
    keys = ("overall", "owner", "topic", "jurisdiction", "date", "document_type", "issuer")
    return {key: _bounded_confidence(raw.get(key), default=0.0 if key == "overall" else _bounded_confidence(raw.get("overall"), default=0.0)) for key in keys}


def _bounded_confidence(value: object, *, default: float) -> float:
    parsed = _optional_float(value)
    if parsed is None:
        return default
    return min(1.0, max(0.0, parsed))


def _summary(value: object, *, unreliable: bool) -> str:
    text = _optional_text(value)
    if not text:
        text = "No reliable content summary was available."
    text = " ".join(text.split())[:600]
    if unreliable and not text.startswith("UNRELIABLE - review required:"):
        text = f"UNRELIABLE - review required: {text}"
    return text


def _classification_path(owner: str | None, topic: str | None, explicit: object) -> str:
    if isinstance(explicit, str) and explicit.strip():
        return _bounded_path(explicit)
    parts = [owner or "unknown_owner", topic or "unknown_topic"]
    return _bounded_path("/".join(parts))


def _human_storage_path(raw: object, owner: str | None, topic: str | None, document_date: object) -> str:
    if isinstance(raw, str) and raw.strip():
        return _bounded_path(raw)
    year = "unknown_year"
    if isinstance(document_date, str) and re.match(r"^\d{4}", document_date):
        year = document_date[:4]
    return _bounded_path("/".join((owner or "unknown_owner", "documents", topic or "unknown_topic", year)))


def _bounded_path(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ /:-]+", "_", value).strip(" /")
    cleaned = re.sub(r"/+", "/", cleaned)
    if not cleaned or cleaned.startswith("/") or ".." in cleaned or re.search(r"(?i)(token|bearer|cookie|password|secret)", cleaned):
        raise ScanReportError("human_storage_path_invalid")
    return cleaned[:240]


def _physical_page_count(package: Mapping[str, Any]) -> int:
    if isinstance(package.get("physical_page_count"), int):
        count = int(package["physical_page_count"])
    else:
        physical_pages = package.get("physical_pages")
        if not isinstance(physical_pages, Sequence) or isinstance(physical_pages, (str, bytes)):
            raise ScanReportError("physical_page_count_missing")
        count = len(physical_pages)
    if count < 0 or count > 5000:
        raise ScanReportError("physical_page_count_invalid")
    return count


def _pages(value: object) -> list[int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ScanReportError("pages_invalid")
    pages = [int(item) for item in value]
    if any(page < 1 for page in pages):
        raise ScanReportError("pages_invalid")
    return pages


def _page_range(pages: Sequence[int]) -> str:
    if not pages:
        return ""
    if list(pages) == list(range(min(pages), max(pages) + 1)):
        return f"{min(pages)}-{max(pages)}" if len(pages) > 1 else str(pages[0])
    return ",".join(str(page) for page in pages)


def _overall_status(*, completed: int, review: int, failed: int, total: int) -> str:
    if failed and completed + review > 0:
        return "partial_success"
    if failed:
        return "error"
    if review:
        return "review_required"
    if completed == total:
        return "success"
    return "awaiting_additional_pass"


def _safe_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.match(value):
        raise ScanReportError(f"{field}_invalid")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:240] if text else None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _validate_delivery_status(status: str) -> None:
    if status not in DELIVERY_STATUSES:
        raise ScanReportError("delivery_status_invalid")


def _safe_reason(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value or "unknown"))
    return text[:96] or "unknown"


def _public_error_reason(exc: Exception) -> str:
    raw = str(exc) or exc.__class__.__name__
    if re.search(r"(?i)(token|secret|bearer|cookie|password|credential)", raw):
        return exc.__class__.__name__ or "telegram_delivery_failed"
    return _safe_reason(raw)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_from_timestamp(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
