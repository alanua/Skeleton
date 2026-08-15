from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.family_document_runtime import ProjectionOutbox, ReceiptOutbox
from core.family_document_sinks import CalendarSink, MemoryGatewaySink, SinkError, build_private_mutation
from core.family_document_sources import ApprovedRoot, SourceError, SourceReference, inventory_sources, resolve_source
from core.family_document_taxonomy import (
    APPROVED_EVENT_TYPES,
    COUNTRY_RULES,
    DOCUMENT_TYPE_RULES,
    SERVICE_FOLDERS,
    TOPIC_RULES,
    TOPICS,
    Evidence,
    extract_amounts,
    extract_document_date,
    extract_event_candidates,
    extract_identifiers,
    extract_issuer,
    normalize_text,
    score_unique,
)
from core.local_document_ocr import LocalDocumentOcr, OcrError, OcrResult, sha256_file


class IntakeError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class Person:
    person_id: str
    aliases: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.person_id or "/" in self.person_id or "\\" in self.person_id:
            raise IntakeError("person_id_invalid")
        aliases = tuple(alias.strip() for alias in self.aliases if alias.strip())
        if not aliases:
            raise IntakeError("person_aliases_required")
        object.__setattr__(self, "aliases", aliases)


@dataclass(frozen=True)
class IntakeConfig:
    people: tuple[Person, ...]
    approved_roots: tuple[ApprovedRoot, ...]
    archive_root: Path
    memory_sink: MemoryGatewaySink
    calendar_sink: CalendarSink
    projection_outbox: ProjectionOutbox
    receipt_outbox: ReceiptOutbox
    ocr: LocalDocumentOcr
    record_revision: str = "family-document-v1"

    def __post_init__(self) -> None:
        if len(self.people) != 3 or len({person.person_id for person in self.people}) != 3:
            raise IntakeError("exactly_three_people_required")
        if not self.approved_roots:
            raise IntakeError("approved_roots_required")
        archive_input = Path(self.archive_root).expanduser()
        if _has_symlink_component(archive_input):
            raise IntakeError("archive_root_symlinked")
        archive = archive_input.resolve(strict=False)
        archive.mkdir(parents=True, exist_ok=True, mode=0o700)
        object.__setattr__(self, "archive_root", archive)
        if not self.record_revision or len(self.record_revision) > 128:
            raise IntakeError("record_revision_invalid")


@dataclass(frozen=True)
class DocumentPlan:
    source: SourceReference
    ocr: OcrResult
    ready: bool
    review_reasons: tuple[str, ...]
    archive_relative_path: str
    archive_target: Path
    record: Mapping[str, Any]
    calendar_events: tuple[Mapping[str, Any], ...]
    version_fingerprint: str

    def private_dict(self) -> dict[str, object]:
        return {
            "source": self.source.private_dict(),
            "ready": self.ready,
            "review_reasons": list(self.review_reasons),
            "archive_relative_path": self.archive_relative_path,
            "record": dict(self.record),
            "calendar_events": [dict(event) for event in self.calendar_events],
            "version_fingerprint": self.version_fingerprint,
        }


class DocumentProcessor:
    def __init__(self, config: IntakeConfig) -> None:
        self.config = config

    def plan(self, source: Path) -> DocumentPlan:
        try:
            reference = resolve_source(source, self.config.approved_roots)
            ocr = self.config.ocr.extract(reference.absolute_path)
        except SourceError as exc:
            raise IntakeError(exc.reason_code) from exc
        except OcrError as exc:
            raise IntakeError(exc.reason_code) from exc

        text = ocr.corrected_text
        normalized = normalize_text(text)
        subject_evidence = self._subjects(normalized)
        subjects = [item["person_id"] for item in subject_evidence if item["matched"]]
        principal = subjects[0] if len(subjects) == 1 else None
        topic = score_unique(normalized, TOPIC_RULES)
        jurisdiction = score_unique(normalized, COUNTRY_RULES)
        document_type = score_unique(normalized, DOCUMENT_TYPE_RULES)
        issuer = extract_issuer(text)
        document_date, date_precision, date_evidence = extract_document_date(normalized)
        identifiers = extract_identifiers(text)
        amounts = extract_amounts(text)
        event_candidates = extract_event_candidates(text)
        deadlines = [candidate.to_dict() for candidate in event_candidates if candidate.event_type == "deadline"]

        review_reasons: list[str] = []
        if principal is None:
            review_reasons.append("principal_subject_ambiguous")
        if topic.value not in TOPICS or topic.confidence < 0.60:
            review_reasons.append("topic_ambiguous")
        if jurisdiction.value is None or jurisdiction.confidence < 0.60:
            review_reasons.append("jurisdiction_ambiguous")
        if document_type.value is None or document_type.confidence < 0.60:
            review_reasons.append("document_type_ambiguous")
        if issuer.value is None or issuer.confidence < 0.55:
            review_reasons.append("issuer_ambiguous")

        year = document_date[:4] if document_date else "Без дати"
        visible_name = normalized_filename(
            document_date,
            date_precision,
            document_type.value or "document",
            issuer.value or "unknown issuer",
            reference.absolute_path.suffix,
        )
        if review_reasons:
            relative = Path(SERVICE_FOLDERS[2], visible_name)
        else:
            relative = Path(principal or "review", topic.value or "review", jurisdiction.value or "review", year, visible_name)
        archive_target = _safe_archive_target(self.config.archive_root, relative)

        source_identity = "source:" + hashlib.sha256(
            f"{reference.root_alias}\x1f{reference.relative_path}".encode("utf-8")
        ).hexdigest()[:48]
        document_id = "document:" + hashlib.sha256(
            f"{source_identity}\x1f{ocr.source_sha256}".encode("utf-8")
        ).hexdigest()[:48]
        version_fingerprint = hashlib.sha256(
            "\x1f".join(
                (
                    principal or "review",
                    topic.value or "review",
                    jurisdiction.value or "review",
                    document_type.value or "document",
                    issuer.value or "unknown",
                )
            ).encode("utf-8")
        ).hexdigest()

        calendar_events: list[Mapping[str, Any]] = []
        if principal is not None:
            for candidate in event_candidates:
                if candidate.event_type not in APPROVED_EVENT_TYPES or candidate.confidence < 0.80:
                    continue
                event_id = "family-document-event:" + hashlib.sha256(
                    f"{ocr.source_sha256}\x1f{candidate.event_type}\x1f{candidate.date}\x1f{principal}".encode("utf-8")
                ).hexdigest()[:48]
                calendar_events.append(
                    {
                        "schema": "skeleton.family_document_event.v1",
                        "event_id": event_id,
                        "event_type": candidate.event_type,
                        "date": candidate.date,
                        "principal_subject": principal,
                        "document_id": document_id,
                        "confidence": candidate.confidence,
                        "evidence_hash": hashlib.sha256(candidate.evidence.encode("utf-8")).hexdigest(),
                        "privacy": "private",
                        "attendees": [],
                        "conference": None,
                    }
                )

        field_confidence = {
            "principal_subject": 0.95 if principal else 0.25,
            "all_subjects": max((float(item["confidence"]) for item in subject_evidence), default=0.0),
            "topic": topic.confidence,
            "jurisdiction": jurisdiction.confidence,
            "document_type": document_type.confidence,
            "issuer": issuer.confidence,
            "document_date": date_evidence.confidence,
            "identifiers": 0.90 if identifiers else 0.0,
            "amounts": 0.85 if amounts else 0.0,
            "deadlines": max((float(item["confidence"]) for item in deadlines), default=0.0),
        }
        field_evidence = {
            "subjects": subject_evidence,
            "topic": topic.to_dict(),
            "jurisdiction": jurisdiction.to_dict(),
            "document_type": document_type.to_dict(),
            "issuer": issuer.to_dict(),
            "document_date": date_evidence.to_dict(),
        }
        record: dict[str, Any] = {
            "schema": "skeleton.family_document_record.v1",
            "record_revision": self.config.record_revision,
            "document_id": document_id,
            "binary_sha256": ocr.source_sha256,
            "byte_size": reference.byte_size,
            "source": {
                "source_identity": source_identity,
                "root_alias": reference.root_alias,
                "absolute_path": str(reference.absolute_path),
                "relative_path": reference.relative_path,
                "mtime_ns": reference.mtime_ns,
                "byte_size": reference.byte_size,
            },
            "ocr": ocr.private_dict(),
            "principal_subject": principal,
            "all_subjects": subjects,
            "topic": topic.value,
            "jurisdiction_country": jurisdiction.value,
            "document_date": document_date,
            "document_date_precision": date_precision,
            "document_type": document_type.value,
            "issuer": issuer.value,
            "identifiers": identifiers,
            "amounts": amounts,
            "deadlines": deadlines,
            "field_confidence": field_confidence,
            "field_evidence": field_evidence,
            "archive": {
                "relative_path": relative.as_posix(),
                "sha256": ocr.source_sha256,
                "readback_verified": False,
            },
            "duplicate_relations": [],
            "version_relations": [],
            "version_fingerprint": version_fingerprint,
            "event_candidates": [dict(event) for event in calendar_events],
            "review": {"required": bool(review_reasons), "reason_codes": review_reasons},
            "projection": {"status": "PENDING"},
        }
        return DocumentPlan(
            source=reference,
            ocr=ocr,
            ready=not review_reasons,
            review_reasons=tuple(review_reasons),
            archive_relative_path=relative.as_posix(),
            archive_target=archive_target,
            record=record,
            calendar_events=tuple(calendar_events),
            version_fingerprint=version_fingerprint,
        )

    def process(self, source: Path, *, dry_run: bool = False) -> Mapping[str, object]:
        try:
            plan = self.plan(source)
        except IntakeError as exc:
            return public_receipt("BLOCKED", exc.reason_code, {"planned": 0, "written": 0})
        if not plan.ready:
            return public_receipt(
                "REVIEW",
                "review_required",
                {"planned": 1, "review": 1, "written": 0, "event_candidates": len(plan.calendar_events)},
            )
        if dry_run:
            return public_receipt(
                "DONE",
                "dry_run_complete",
                {"planned": 1, "written": 0, "event_candidates": len(plan.calendar_events)},
            )
        try:
            archive_path, duplicate = archive_verified(plan, self.config.archive_root)
            record = json.loads(json.dumps(plan.record))
            archive = record["archive"]
            archive["absolute_path"] = str(archive_path)
            archive["readback_verified"] = True
            memory = self.config.memory_sink.commit_and_readback(
                record,
                source_hash=plan.ocr.source_sha256,
            )
            projection_key = f"{memory['canonical_ref']}:projection"
            self.config.projection_outbox.enqueue(projection_key, stable_hash(record))
            calendar_done = 0
            calendar_failed = 0
            for event in plan.calendar_events:
                try:
                    self.config.calendar_sink.upsert(event)
                    calendar_done += 1
                except SinkError:
                    calendar_failed += 1
            reason = "calendar_degraded" if calendar_failed else "projection_pending"
            return public_receipt(
                "DONE",
                reason,
                {
                    "planned": 1,
                    "written": 0 if duplicate else 1,
                    "duplicates": 1 if duplicate else 0,
                    "canonical_commits": 1,
                    "canonical_readbacks": 1,
                    "calendar_events": calendar_done,
                    "calendar_failed": calendar_failed,
                    "projection_pending": 1,
                },
            )
        except (IntakeError, SinkError) as exc:
            return public_receipt("BLOCKED", exc.reason_code, {"planned": 1, "written": 0})

    def reconcile(self, *, max_files: int = 10000) -> tuple[dict[str, object], dict[str, object]]:
        plans: list[DocumentPlan] = []
        blocked = 0
        for reference in inventory_sources(self.config.approved_roots, max_files=max_files):
            try:
                plans.append(self.plan(reference.absolute_path))
            except IntakeError:
                blocked += 1
        digest_groups: dict[str, list[int]] = {}
        version_groups: dict[str, list[int]] = {}
        for index, plan in enumerate(plans):
            digest_groups.setdefault(plan.ocr.source_sha256, []).append(index)
            version_groups.setdefault(plan.version_fingerprint, []).append(index)
        items: list[dict[str, object]] = []
        for index, plan in enumerate(plans):
            record = json.loads(json.dumps(plan.record))
            duplicates = [plans[position].record["document_id"] for position in digest_groups[plan.ocr.source_sha256] if position != index]
            versions = [plans[position].record["document_id"] for position in version_groups[plan.version_fingerprint] if position != index and plans[position].ocr.source_sha256 != plan.ocr.source_sha256]
            record["duplicate_relations"] = duplicates
            record["version_relations"] = versions
            mutation = build_private_mutation(
                record,
                approval_ref="operator.family_document.reconciliation",
                source_hash=plan.ocr.source_sha256,
            )
            payload = mutation["payload"]
            items.append(
                {
                    "source": plan.source.private_dict(),
                    "ready": plan.ready,
                    "review_reasons": list(plan.review_reasons),
                    "archive_relative_path": plan.archive_relative_path,
                    "document_id": record["document_id"],
                    "fact_namespace": payload["fact_namespace"],
                    "fact_id": payload["fact_id"],
                    "idempotency_key": payload["idempotency_key"],
                    "duplicate_relations": duplicates,
                    "version_relations": versions,
                    "calendar_event_ids": [event["event_id"] for event in plan.calendar_events],
                    "record": record,
                }
            )
        packet = {
            "schema": "skeleton.family_document.reconciliation_packet.v1",
            "zero_side_effect": True,
            "items": items,
        }
        packet_hash = stable_hash(packet)
        packet["packet_hash"] = packet_hash
        counts = {
            "inventory": len(plans) + blocked,
            "planned": len(plans),
            "ready": sum(1 for plan in plans if plan.ready),
            "review": sum(1 for plan in plans if not plan.ready),
            "blocked": blocked,
            "duplicate_groups": sum(1 for values in digest_groups.values() if len(values) > 1),
            "version_groups": sum(1 for values in version_groups.values() if len({plans[index].ocr.source_sha256 for index in values}) > 1),
            "calendar_events": sum(len(plan.calendar_events) for plan in plans),
        }
        return packet, public_receipt("DONE", "reconciliation_packet_ready", counts)

    def _subjects(self, normalized_text: str) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for person in self.config.people:
            hits = [alias for alias in person.aliases if alias.casefold() in normalized_text]
            result.append(
                {
                    "person_id": person.person_id,
                    "matched": bool(hits),
                    "confidence": min(0.99, 0.75 + 0.08 * len(hits)) if hits else 0.0,
                    "aliases": hits,
                }
            )
        return result


def archive_verified(plan: DocumentPlan, archive_root: Path) -> tuple[Path, bool]:
    target = _safe_archive_target(archive_root, Path(plan.archive_relative_path))
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists():
        if target.is_symlink():
            raise IntakeError("archive_target_symlinked")
        if sha256_file(target) == plan.ocr.source_sha256:
            return target, True
        target = _safe_archive_target(
            archive_root,
            Path(
                SERVICE_FOLDERS[1],
                plan.version_fingerprint[:16],
                f"{target.stem}--{plan.ocr.source_sha256[:12]}{target.suffix}",
            ),
        )
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.exists():
            if sha256_file(target) == plan.ocr.source_sha256:
                return target, True
            raise IntakeError("archive_collision")
    temporary = target.with_name(target.name + f".{os.getpid()}.part")
    try:
        with plan.source.absolute_path.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if sha256_file(temporary) != plan.ocr.source_sha256:
            raise IntakeError("archive_write_hash_mismatch")
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_file() and sha256_file(target) == plan.ocr.source_sha256:
                return target, True
            raise IntakeError("archive_collision")
        temporary.unlink(missing_ok=True)
        target.chmod(0o600)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    if sha256_file(target) != plan.ocr.source_sha256:
        raise IntakeError("archive_readback_failed")
    return target, False


def normalized_filename(
    date_value: str | None,
    precision: str | None,
    document_type: str,
    issuer: str,
    suffix: str,
) -> str:
    prefix = date_value if date_value and precision else "Без дати"
    clean_type = _clean_component(document_type)
    clean_issuer = _clean_component(issuer)
    extension = suffix.casefold() if suffix else ".bin"
    return f"{prefix} — {clean_type} — {clean_issuer}{extension}"


def public_receipt(status: str, reason_code: str, counts: Mapping[str, int]) -> dict[str, object]:
    allowed_reasons = {
        "review_required",
        "dry_run_complete",
        "projection_pending",
        "calendar_degraded",
        "reconciliation_packet_ready",
        "source_unavailable",
        "source_unsupported",
        "source_partial",
        "source_symlink_rejected",
        "source_outside_approved_roots",
        "source_empty",
        "ocr_format_unsupported",
        "ocr_empty",
        "text_read_failed",
        "pdf_ocr_failed",
        "pdf_ocr_empty",
        "pdftotext_failed",
        "tesseract_failed",
        "office_conversion_failed",
        "office_conversion_output_invalid",
        "archive_target_symlinked",
        "archive_collision",
        "archive_write_hash_mismatch",
        "archive_readback_failed",
        "memory_mutation_failed",
        "memory_exact_read_failed",
        "memory_exact_read_value_missing",
        "memory_exact_read_mismatch",
        "calendar_upsert_failed",
        "adapter_failed",
        "adapter_response_invalid",
        "processing_failed",
    }
    safe_reason = reason_code if reason_code in allowed_reasons else "processing_failed"
    safe_counts: dict[str, int] = {}
    for key, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            continue
        safe_counts[str(key)] = value
    return {
        "schema": "skeleton.family_document_receipt.public.v1",
        "status": status if status in {"DONE", "REVIEW", "BLOCKED", "DEGRADED"} else "BLOCKED",
        "reason_code": safe_reason,
        "counts": safe_counts,
    }


def stable_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_archive_target(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise IntakeError("archive_relative_path_invalid")
    root = Path(root).resolve(strict=True)
    target = (root / relative).resolve(strict=False)
    if root not in target.parents:
        raise IntakeError("archive_path_escape")
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise IntakeError("archive_target_symlinked")
    return target


def _clean_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9ÄÖÜäöüßА-Яа-яІіЇїЄє ._-]+", "", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:80] or "unknown"


def _has_symlink_component(path: Path) -> bool:
    absolute = path if path.is_absolute() else Path.cwd() / path
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
