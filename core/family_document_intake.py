from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from core.family_document_runtime import (
    FamilyDocumentRuntimeError,
    FileArchiveSink,
    InMemoryCalendarSink,
    InMemoryGatewaySink,
    JsonOperationalJournal,
    public_token,
    sha256_file,
)
from core.family_document_taxonomy import Person, RouteDecision, route_document, slug
from core.local_document_ocr import LocalDocumentOcrError, run_local_ocr


RECEIPT_SCHEMA = "skeleton.family_document_intake.receipt.v1"
RECORD_SCHEMA = "skeleton.family_document_record.v1"
EVENT_SCHEMA = "skeleton.family_document_event.v1"
PARTIAL_SUFFIXES = (".part", ".partial", ".tmp", ".crdownload")
FORBIDDEN_ROOT_MARKERS = ("/desktop/", "/system/", "/windows/", "/secrets/", "/secret/", "/.ssh/", "/src/", "/code/")


class FamilyDocumentIntakeError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class IntakeRequest:
    source_paths: tuple[Path, ...]
    intake_roots: tuple[Path, ...]
    archive_root: Path
    journal_path: Path
    people: tuple[Person, ...]
    dry_run: bool = False
    stable_after_seconds: float = 1.0


def process_intake(
    request: IntakeRequest,
    *,
    memory_sink: Any | None = None,
    calendar_sink: Any | None = None,
    projection_sink: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None,
    archive_sink: FileArchiveSink | None = None,
    ocr_recognizer: Callable[[Path], object] | None = None,
) -> dict[str, object]:
    journal = JsonOperationalJournal(request.journal_path)
    memory = memory_sink or InMemoryGatewaySink()
    calendar = calendar_sink or InMemoryCalendarSink()
    archive = archive_sink or FileArchiveSink(request.archive_root)
    receipts: list[dict[str, object]] = []
    for source in request.source_paths:
        receipts.append(
            _process_one(
                source=Path(source),
                request=request,
                journal=journal,
                archive=archive,
                memory=memory,
                calendar=calendar,
                projection_sink=projection_sink,
                ocr_recognizer=ocr_recognizer,
            )
        )
        if not request.dry_run:
            journal.save()
    return {
        "schema": RECEIPT_SCHEMA,
        "status": "DRY_RUN" if request.dry_run else _summary_status(receipts),
        "processed": len(receipts),
        "receipts": receipts,
    }


def build_request(payload: Mapping[str, Any]) -> IntakeRequest:
    people = tuple(
        Person(
            person_id=str(item.get("person_id", "")),
            display_name=str(item["display_name"]),
            surnames=tuple(str(v) for v in item.get("surnames", ())),
            country=str(item.get("country", "unknown")),
        )
        for item in payload.get("people", ())
    )
    return IntakeRequest(
        source_paths=tuple(Path(p) for p in payload["source_paths"]),
        intake_roots=tuple(Path(p) for p in payload["intake_roots"]),
        archive_root=Path(payload["archive_root"]),
        journal_path=Path(payload["journal_path"]),
        people=people,
        dry_run=bool(payload.get("dry_run", False)),
        stable_after_seconds=float(payload.get("stable_after_seconds", 1.0)),
    )


def deterministic_relative_path(
    *,
    route: RouteDecision,
    document_date: date,
    source_suffix: str,
    content_sha256: str,
    occupied_names: set[str] | None = None,
) -> Path:
    occupied = occupied_names or set()
    base = f"{document_date.isoformat()}_{route.topic}_{content_sha256[:12]}"
    suffix = source_suffix.lower() or ".bin"
    candidate = f"{base}{suffix}"
    ordinal = 2
    while candidate in occupied:
        candidate = f"{base}-{ordinal}{suffix}"
        ordinal += 1
    person = route.person_id if route.status == "READY" else "review"
    return Path(person) / route.topic / route.country / str(document_date.year) / candidate


def _process_one(
    *,
    source: Path,
    request: IntakeRequest,
    journal: JsonOperationalJournal,
    archive: FileArchiveSink,
    memory: Any,
    calendar: Any,
    projection_sink: Callable[[Mapping[str, object]], Mapping[str, object]] | None,
    ocr_recognizer: Callable[[Path], object] | None,
) -> dict[str, object]:
    try:
        resolved = _validate_source_path(source, request.intake_roots)
        stable = _stable_status(resolved, request.stable_after_seconds)
        if stable is not None:
            return _receipt("SKIPPED", stable, resolved)
        digest = sha256_file(resolved)
        cluster_id = f"sha256:{digest}"
        entry = journal.record(cluster_id)
        if entry.get("status") == "DONE":
            return _public_done_receipt(cluster_id, entry, duplicate=True)
        try:
            ocr = run_local_ocr(resolved, recognizer=ocr_recognizer)
        except LocalDocumentOcrError as exc:
            entry.update({"status": "FAILED", "reason_code": exc.reason_code, "cluster_token": public_token(cluster_id)})
            return _receipt("FAILED", exc.reason_code, resolved, cluster_id=cluster_id)
        document_date = _document_date(ocr.text) or date.fromtimestamp(resolved.stat().st_mtime)
        route = route_document(
            {"text": ocr.text, "filename": resolved.name, "country": ""},
            request.people,
        )
        relative = deterministic_relative_path(
            route=route,
            document_date=document_date,
            source_suffix=resolved.suffix,
            content_sha256=digest,
            occupied_names=set(entry.get("occupied_names", ())),
        )
        record = _record(cluster_id, digest, route, document_date, relative, ocr.engine)
        if request.dry_run:
            return _public_receipt("DRY_RUN", cluster_id, route, document_date, side_effects=[])
        if entry.get("archive_verified") is not True:
            archive_result = archive.put(
                cluster_id=cluster_id,
                relative_path=relative,
                source=resolved,
                expected_sha256=digest,
                dry_run=False,
            )
            if archive_result.get("verified") is not True:
                raise FamilyDocumentIntakeError("ARCHIVE_VERIFY_FAILED", "archive write was not verified")
            entry.update({"archive_verified": True, "archive": archive_result, "relative_token": public_token(str(relative))})
            journal.save()
        if entry.get("memory_committed") is not True:
            result = memory.commit(record)
            entry.update({"memory_committed": True, "memory": dict(result), "record_token": public_token(cluster_id)})
            journal.save()
        if projection_sink is not None and entry.get("projection_status") != "PROJECTED":
            try:
                projection_sink(record)
            except Exception:
                entry.update({"projection_status": "FAILED", "status": "PARTIAL", "reason_code": "PROJECTION_FAILED"})
                return _public_receipt("PARTIAL", cluster_id, route, document_date, reason_code="PROJECTION_FAILED")
            entry["projection_status"] = "PROJECTED"
        events = _semantic_events(cluster_id, ocr.text, document_date, route)
        if events and entry.get("calendar_upserted") is not True:
            for event in events:
                calendar.upsert(str(event["event_id"]), event)
            entry["calendar_upserted"] = True
        entry.update({"status": "DONE", "route_status": route.status, "reason_code": route.reason_code})
        return _public_done_receipt(cluster_id, entry, duplicate=False, route=route, document_date=document_date)
    except FamilyDocumentIntakeError as exc:
        status = "SKIPPED" if exc.reason_code == "PARTIAL_FILE" else "FAILED"
        return _receipt(status, exc.reason_code, source)
    except FamilyDocumentRuntimeError as exc:
        return _receipt("FAILED", exc.reason_code, source)


def _validate_source_path(source: Path, intake_roots: Sequence[Path]) -> Path:
    resolved = source.expanduser().resolve(strict=False)
    lowered = str(resolved).casefold()
    if any(marker in lowered for marker in FORBIDDEN_ROOT_MARKERS):
        raise FamilyDocumentIntakeError("SOURCE_ROOT_REJECTED", "source root is not allowed")
    if source.suffix.lower() in PARTIAL_SUFFIXES or any(str(source).lower().endswith(s) for s in PARTIAL_SUFFIXES):
        raise FamilyDocumentIntakeError("PARTIAL_FILE", "partial file is not processed")
    roots = [root.expanduser().resolve(strict=False) for root in intake_roots]
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise FamilyDocumentIntakeError("SOURCE_ROOT_REJECTED", "source is outside allowed intake roots")
    if not resolved.exists() or not resolved.is_file():
        raise FamilyDocumentIntakeError("SOURCE_UNAVAILABLE", "source file is unavailable")
    return resolved


def _stable_status(path: Path, stable_after_seconds: float) -> str | None:
    stat1 = path.stat()
    if stat1.st_size <= 0:
        return "UNSTABLE_FILE"
    time.sleep(0)
    stat2 = path.stat()
    if stat1.st_size != stat2.st_size or stat1.st_mtime_ns != stat2.st_mtime_ns:
        return "UNSTABLE_FILE"
    if time.time() - stat2.st_mtime < stable_after_seconds:
        return "UNSTABLE_FILE"
    return None


def _document_date(text: str) -> date | None:
    match = re.search(r"\b(20\d{2}|19\d{2})[-/.](0[1-9]|1[0-2])[-/.]([0-2]\d|3[01])\b", text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _record(
    cluster_id: str,
    digest: str,
    route: RouteDecision,
    document_date: date,
    relative_path: Path,
    ocr_engine: str,
) -> dict[str, object]:
    return {
        "schema": RECORD_SCHEMA,
        "cluster_id": cluster_id,
        "content_sha256": digest,
        "route_status": route.status,
        "person_token": public_token(route.person_id),
        "topic": route.topic,
        "country": route.country,
        "document_date": document_date.isoformat(),
        "archive_relative_path": str(relative_path),
        "ocr_engine": ocr_engine,
    }


def _semantic_events(cluster_id: str, text: str, document_date: date, route: RouteDecision) -> list[dict[str, object]]:
    lowered = text.casefold()
    if not any(word in lowered for word in ("appointment", "deadline", "due date", "expires", "renewal")):
        return []
    return [
        {
            "schema": EVENT_SCHEMA,
            "event_id": f"{cluster_id}:semantic:{document_date.isoformat()}",
            "summary": f"Family document {route.topic}",
            "date": document_date.isoformat(),
            "source_cluster_token": public_token(cluster_id),
        }
    ]


def _receipt(status: str, reason_code: str, source: Path, *, cluster_id: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "status": status,
        "reason_code": reason_code,
        "source_token": public_token(str(source)),
    }
    if cluster_id:
        payload["cluster_token"] = public_token(cluster_id)
    return payload


def _public_receipt(
    status: str,
    cluster_id: str,
    route: RouteDecision,
    document_date: date,
    *,
    reason_code: str | None = None,
    side_effects: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema": RECEIPT_SCHEMA,
        "status": status,
        "reason_code": reason_code or route.reason_code,
        "cluster_token": public_token(cluster_id),
        "source_token": public_token(cluster_id + ":source"),
        "route_status": route.status,
        "topic": route.topic,
        "country": route.country,
        "document_year": document_date.year,
        "side_effects": ["archive", "memory", "calendar"] if side_effects is None else side_effects,
    }


def _public_done_receipt(
    cluster_id: str,
    entry: Mapping[str, object],
    *,
    duplicate: bool,
    route: RouteDecision | None = None,
    document_date: date | None = None,
) -> dict[str, object]:
    if route is not None and document_date is not None:
        receipt = _public_receipt("DONE", cluster_id, route, document_date)
    else:
        receipt = {"schema": RECEIPT_SCHEMA, "status": "DONE", "cluster_token": public_token(cluster_id)}
    receipt["idempotency"] = "DUPLICATE_EXISTING" if duplicate else "NEW"
    receipt["archive_verified"] = bool(entry.get("archive_verified"))
    receipt["memory_committed"] = bool(entry.get("memory_committed"))
    return receipt


def _summary_status(receipts: Sequence[Mapping[str, object]]) -> str:
    if any(receipt.get("status") == "FAILED" for receipt in receipts):
        return "FAILED"
    if any(receipt.get("status") in {"PARTIAL", "SKIPPED"} for receipt in receipts):
        return "PARTIAL"
    return "DONE"
