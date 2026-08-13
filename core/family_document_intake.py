from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from core.family_document_sinks import (
    DurableProjectionOutbox,
    TelegramNotificationOutbox,
    TelegramSender,
    VerifiedArchive,
    aggregate_receipt,
    private_put_request,
)
from core.family_document_sources import ApprovedLocalSourceInventory, StableFileGate
from core.family_document_taxonomy import (
    TAXONOMY_VERSION,
    classify_text_locally,
    deterministic_document_name,
)
from core.local_document_ocr import extract_local_document

FAMILY_DOCUMENT_RECORD_SCHEMA = "skeleton.family_document_record.v1"
FAMILY_DOCUMENT_REQUEST_SCHEMA = "skeleton.family_document_intake_request.v1"
CANONICAL_FACT_NAMESPACE = "family_document"
_SAFE_APPROVAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")


class FamilyDocumentIntakeError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class MemoryGatewayProtocol(Protocol):
    def execute(self, request: Mapping[str, Any]) -> dict[str, object]: ...


@dataclass(frozen=True)
class FamilyDocumentIntakeConfig:
    inbox_roots: tuple[Path, ...]
    archive_root: Path
    runtime_root: Path
    quarantine_root: Path
    subject_aliases: tuple[str, str, str]
    memory_gate_adapter_command: tuple[str, ...]
    approval_ref: str
    stable_age_seconds: float = 1.0
    max_attempts: int = 3
    backoff_seconds: float = 0.25
    source_kind: str = "mfp"
    telegram_bot_env: str = "SKELETON_TG_BOT"
    telegram_chat_env: str = "SKELETON_TG_CHAT"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FamilyDocumentIntakeConfig":
        if value.get("schema") != "skeleton.family_document_runtime_config.v1":
            raise FamilyDocumentIntakeError("config_schema_invalid", "runtime config schema is invalid")
        aliases = tuple(str(item).strip() for item in value.get("subject_aliases", ()))
        if len(aliases) != 3 or any(not alias for alias in aliases) or len(set(alias.casefold() for alias in aliases)) != 3:
            raise FamilyDocumentIntakeError("subject_aliases_invalid", "exactly three distinct subject aliases are required")
        roots = tuple(Path(str(item)).expanduser().resolve() for item in value.get("inbox_roots", ()))
        if not roots:
            raise FamilyDocumentIntakeError("inbox_roots_required", "at least one inbox root is required")
        memory_gate = value.get("memory_gate")
        if not isinstance(memory_gate, Mapping):
            raise FamilyDocumentIntakeError("memory_gate_required", "memory_gate configuration is required")
        command_raw = memory_gate.get("adapter_command")
        if not isinstance(command_raw, list) or not command_raw or any(not isinstance(item, str) or not item for item in command_raw):
            raise FamilyDocumentIntakeError("memory_gate_adapter_command_invalid", "memory_gate.adapter_command must be a non-empty argv list")
        approval_ref = str(value.get("approval_ref", "")).strip()
        if not _SAFE_APPROVAL_RE.fullmatch(approval_ref) or approval_ref.casefold().startswith("synthetic"):
            raise FamilyDocumentIntakeError("approval_ref_invalid", "a non-synthetic approval_ref is required")
        source_kind = str(value.get("source_kind", "mfp")).strip()
        if source_kind not in {"mfp", "local_import"}:
            raise FamilyDocumentIntakeError("source_kind_invalid", "source_kind is invalid")
        stable_age_seconds = float(value.get("stable_age_seconds", 1.0))
        max_attempts = int(value.get("max_attempts", 3))
        backoff_seconds = float(value.get("backoff_seconds", 0.25))
        if stable_age_seconds < 0 or stable_age_seconds > 300:
            raise FamilyDocumentIntakeError("stable_age_seconds_invalid", "stable age is outside bounds")
        if max_attempts < 1 or max_attempts > 10:
            raise FamilyDocumentIntakeError("max_attempts_invalid", "max attempts is outside bounds")
        if backoff_seconds < 0 or backoff_seconds > 60:
            raise FamilyDocumentIntakeError("backoff_seconds_invalid", "backoff is outside bounds")
        return cls(
            inbox_roots=roots,
            archive_root=Path(str(value["archive_root"])).expanduser().resolve(),
            runtime_root=Path(str(value["runtime_root"])).expanduser().resolve(),
            quarantine_root=Path(str(value["quarantine_root"])).expanduser().resolve(),
            subject_aliases=aliases,  # type: ignore[arg-type]
            memory_gate_adapter_command=tuple(command_raw),
            approval_ref=approval_ref,
            stable_age_seconds=stable_age_seconds,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
            source_kind=source_kind,
            telegram_bot_env=str(value.get("telegram_bot_env", "SKELETON_TG_BOT")),
            telegram_chat_env=str(value.get("telegram_chat_env", "SKELETON_TG_CHAT")),
        )


class FamilyDocumentJournal:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / "family_document_intake_journal.jsonl"

    def append(self, event: Mapping[str, Any]) -> None:
        row = {"schema": "skeleton.family_document_intake_journal_event.v1", **dict(event)}
        line = json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            os.chmod(self.path, 0o600)
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def records(self) -> tuple[dict[str, object], ...]:
        if not self.path.exists():
            return ()
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
        return tuple(rows)

    def done_sha256(self) -> set[str]:
        return {str(row["sha256"]) for row in self.records() if row.get("stage") == "DONE" and isinstance(row.get("sha256"), str)}

    def attempts(self, sha256: str) -> int:
        return sum(1 for row in self.records() if row.get("sha256") == sha256 and row.get("stage") == "FAILED")


class FamilyDocumentIntake:
    def __init__(self, config: FamilyDocumentIntakeConfig, gateway: MemoryGatewayProtocol) -> None:
        self.config = config
        self.gateway = gateway
        self.journal = FamilyDocumentJournal(config.runtime_root)
        self.archive = VerifiedArchive(config.archive_root)
        self.outbox = DurableProjectionOutbox(config.runtime_root)
        self.notifications = TelegramNotificationOutbox(config.runtime_root)
        self.telegram = TelegramSender(bot_env=config.telegram_bot_env, chat_env=config.telegram_chat_env)
        self.quarantine = config.quarantine_root
        self.quarantine.mkdir(mode=0o700, parents=True, exist_ok=True)

    def process_one(self) -> dict[str, object] | None:
        self.flush_notifications()
        inventory = ApprovedLocalSourceInventory(tuple(self.config.inbox_roots))
        gate = StableFileGate(min_age_seconds=self.config.stable_age_seconds)
        for item in inventory.iter_candidates():
            stable, stable_meta = gate.check(item.path)
            if not stable:
                continue
            return self.process_file(item.path, stable_meta=stable_meta)
        return None

    def process_file(self, path: str | Path, *, stable_meta: Mapping[str, object] | None = None) -> dict[str, object]:
        source = Path(path).expanduser().resolve()
        extraction = extract_local_document(source)
        sha256 = extraction.source_sha256
        if sha256 in self.journal.done_sha256():
            self.journal.append({"stage": "DUPLICATE", "sha256": sha256})
            self.flush_notifications()
            return aggregate_receipt(status="DUPLICATE", duplicate=True, event_count=0)
        self.journal.append({"stage": "CLAIMED", "sha256": sha256, "source_extension": extraction.extension})
        self._notify(sha256=sha256, phase="intake", status="INTAKE", text="СК: скан прийнято, обробка розпочата.")
        try:
            record = self._build_record(source, extraction.as_payload(), stable_meta or {})
            if record["route"] != "ACCEPT":
                self._quarantine(record, reason="REVIEW_REQUIRED")
                self.journal.append({"stage": "QUARANTINED", "sha256": sha256, "reason": "REVIEW_REQUIRED"})
                self._notify(sha256=sha256, phase="terminal", status="REVIEW", text="СК: скан оброблено, потрібна перевірка.")
                return aggregate_receipt(status="REVIEW", duplicate=False, event_count=0)

            extension = extraction.extension if str(extraction.extension).startswith(".") else f".{extraction.extension}"
            archive_name = deterministic_document_name(record, extension=extension)
            archive_receipt = self.archive.write_source_once(source, archive_name, expected_sha256=sha256)
            self.archive.write_metadata_once(f"{archive_name}.json", record)
            self.journal.append(
                {
                    "stage": "ARCHIVED",
                    "sha256": sha256,
                    "archive_sha256": archive_receipt.sha256,
                    "archive_name": archive_name,
                }
            )

            gateway_receipt = self.gateway.execute(
                private_put_request(
                    fact_namespace=CANONICAL_FACT_NAMESPACE,
                    fact_id=str(record["document_id"]),
                    value=record,
                    source_hash=sha256,
                    idempotency_key=str(record["idempotency_key"]),
                    approval_ref=self.config.approval_ref,
                )
            )
            payload = gateway_receipt.get("payload", {})
            canonical_ref = payload.get("canonical_ref") if isinstance(payload, Mapping) else None
            canonical_revision = payload.get("canonical_revision") if isinstance(payload, Mapping) else None
            if not isinstance(canonical_ref, str) or not canonical_ref:
                raise FamilyDocumentIntakeError("canonical_receipt_missing", "MemoryGateway did not return a canonical_ref")
            if not isinstance(canonical_revision, int) or isinstance(canonical_revision, bool) or canonical_revision < 1:
                raise FamilyDocumentIntakeError("canonical_receipt_missing", "MemoryGateway did not return a canonical_revision")

            self.outbox.enqueue(
                {
                    "schema": "skeleton.family_document_projection_request.v1",
                    "canonical_ref": canonical_ref,
                    "canonical_revision": canonical_revision,
                    "source_sha256": sha256,
                    "operation": "upsert",
                    "event_candidates": record["event_candidates"],
                }
            )
            self.journal.append(
                {
                    "stage": "DONE",
                    "sha256": sha256,
                    "canonical_ref": canonical_ref,
                    "canonical_revision": canonical_revision,
                }
            )
            self._notify(sha256=sha256, phase="terminal", status="DONE", text="СК: скан успішно оброблено та збережено.")
            return aggregate_receipt(
                status="DONE",
                duplicate=False,
                event_count=len(record["event_candidates"]) if isinstance(record["event_candidates"], list) else 0,
            )
        except Exception as exc:
            self.journal.append({"stage": "FAILED", "sha256": sha256, "reason": type(exc).__name__})
            if self.journal.attempts(sha256) >= self.config.max_attempts:
                self._notify(sha256=sha256, phase="terminal", status="FAILED", text="СК: обробку скану не завершено; потрібна перевірка.")
            raise
        finally:
            self.flush_notifications()

    def flush_notifications(self) -> dict[str, int]:
        try:
            return self.notifications.flush(self.telegram)
        except Exception:
            return {"delivered": 0, "pending": 1}

    def reconcile_dry_run(self) -> dict[str, object]:
        rows = self.journal.records()
        counts: dict[str, int] = {}
        for row in rows:
            stage = str(row.get("stage", "UNKNOWN"))
            counts[stage] = counts.get(stage, 0) + 1
        archive_files = [path for path in self.config.archive_root.iterdir() if path.is_file()] if self.config.archive_root.exists() else []
        return {
            "schema": "skeleton.family_document_reconcile_receipt.v1",
            "mode": "dry_run",
            "privacy": "aggregate_only",
            "aggregate_counts": {**counts, "archive_files": len(archive_files)},
        }

    def _build_record(
        self,
        source: Path,
        extraction: Mapping[str, object],
        stable_meta: Mapping[str, object],
    ) -> dict[str, object]:
        text = str(extraction.get("text") or "")
        decision = classify_text_locally(text, source_name=source.name)
        matched_subjects = self._matched_subjects(text)
        principal = matched_subjects[0] if len(matched_subjects) == 1 else None
        reason_codes = list(decision.reason_codes)
        if not matched_subjects:
            reason_codes.append("SUBJECT_UNCERTAIN")
        elif len(matched_subjects) > 1:
            reason_codes.append("SUBJECT_AMBIGUOUS")
        route = "ACCEPT" if decision.route == "ACCEPT" and principal is not None else "REVIEW"
        sha256 = str(extraction["source_sha256"])
        document_id = f"sha256-{sha256[:32]}"
        confidence = {
            "overall": decision.confidence if route == "ACCEPT" else min(decision.confidence, 0.79),
            "owner": 0.99 if principal else 0.0,
            "topic": decision.confidence if decision.topic_alias else 0.0,
            "jurisdiction": 0.92 if decision.jurisdiction_country else 0.0,
            "date": 0.92 if decision.document_date else 0.0,
            "document_type": 0.92 if decision.document_type else 0.0,
            "issuer": 0.92 if decision.issuer else 0.0,
        }
        return {
            "schema": FAMILY_DOCUMENT_RECORD_SCHEMA,
            "taxonomy_version": TAXONOMY_VERSION,
            "document_id": document_id,
            "idempotency_key": f"family-document-{sha256}",
            "source_sha256": sha256,
            "sha256": sha256,
            "version_cluster_id": f"source-sha256-{sha256}",
            "duplicate_cluster_key": sha256,
            "source_kind": self.config.source_kind,
            "source_inventory": {"extension": extraction["extension"], "mime_type": extraction["mime_type"]},
            "stable_file_gate": dict(stable_meta),
            "route": route,
            "principal_subject_alias": principal,
            "linked_subject_aliases": list(matched_subjects),
            "topic_alias": decision.topic_alias,
            "jurisdiction_country": decision.jurisdiction_country,
            "document_date": decision.document_date,
            "date_precision": decision.date_precision,
            "document_type": decision.document_type,
            "issuer": decision.issuer,
            "summary": decision.summary,
            "confidence": confidence,
            "evidence": {
                "owner": [{"matched_alias": principal}] if principal else [],
                "topic": [{"topic_alias": decision.topic_alias}] if decision.topic_alias else [],
                "jurisdiction": [{"country": decision.jurisdiction_country}] if decision.jurisdiction_country else [],
                "date": [{"date": decision.document_date}] if decision.document_date else [],
                "document_type": [{"document_type": decision.document_type}] if decision.document_type else [],
                "issuer": [{"issuer": decision.issuer}] if decision.issuer else [],
            },
            "event_candidates": [dict(item) for item in decision.event_candidates],
            "reason_codes": sorted(set(reason_codes)),
            "extraction": {
                "schema": extraction["schema"],
                "extractor": extraction["extractor"],
                "page_count": extraction["page_count"],
                "layout": extraction["layout"],
                "reason_codes": extraction["reason_codes"],
            },
        }

    def _matched_subjects(self, text: str) -> tuple[str, ...]:
        normalized = " ".join(text.casefold().split())
        return tuple(alias for alias in self.config.subject_aliases if " ".join(alias.casefold().split()) in normalized)

    def _notify(self, *, sha256: str, phase: str, status: str, text: str) -> None:
        self.notifications.enqueue_once(key=f"{sha256}:{phase}:{status}", text=text)
        self.flush_notifications()

    def _quarantine(self, record: Mapping[str, object], *, reason: str) -> None:
        name = f"{reason.lower()}-{hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:16]}.json"
        path = self.quarantine / name
        if path.exists():
            return
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                os.chmod(temporary, 0o600)
                json.dump({"reason": reason, "record": dict(record)}, handle, ensure_ascii=True, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
