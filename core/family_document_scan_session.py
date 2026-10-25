"""Explicit duplex producer. Operational state lives in the existing outbox DB.

No timestamps or OCR content participate in pairing. A page index is zero based;
physical sheet numbers are one based. Missing backs must be declared, never inferred.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping

from core.family_document_sources import StableFileGate
from core.local_inference_adapters import InferenceValidationError, validate_json_schema


_TOKEN = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$"}
PROVENANCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["session_id", "scanner_id", "profile_id", "pages"],
    "properties": {
        "session_id": _TOKEN,
        "scanner_id": _TOKEN,
        "profile_id": _TOKEN,
        "pages": {
            "type": "array",
            "minItems": 1,
            "maxItems": 500,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["pass_id", "sheet", "side", "page"],
                "properties": {
                    "pass_id": _TOKEN,
                    "sheet": {"type": "integer", "minimum": 1, "maximum": 500},
                    "side": {"enum": ["front", "back"]},
                    "page": {"type": "integer", "minimum": 1, "maximum": 500},
                },
            },
        },
    },
}


def validate_scan_provenance(value: Mapping[str, Any], page_count: int) -> dict[str, Any]:
    validate_json_schema(value, PROVENANCE_SCHEMA)
    pages = value["pages"]
    if sorted(page["page"] for page in pages) != list(range(1, page_count + 1)):
        raise InferenceValidationError("scan_page_coverage_invalid")
    sides = {(page["pass_id"], page["sheet"], page["side"]) for page in pages}
    if len(sides) != len(pages):
        raise InferenceValidationError("scan_side_duplicate")
    return {**value, "pages": [dict(page) for page in pages]}


def safe_scan_provenance(receipt: Mapping[str, Any]) -> dict[str, Any]:
    page_count = int(receipt["page_count"])
    safe = {
        "session_id": receipt["session_id"],
        "scanner_id": receipt["scanner_id"],
        "profile_id": receipt["profile_id"],
        "pages": [
            {
                "pass_id": slot["pass_id"],
                "sheet": slot["sheet"],
                "side": slot["side"],
                "page": slot["output_page_index"] + 1,
            }
            for slot in receipt["slots"]
            if isinstance(slot.get("output_page_index"), int)
        ],
    }
    return validate_scan_provenance(safe, page_count)


class SessionReview(ValueError):
    """Explicit producer evidence needs operator review."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class PdfCodec:
    """Lazy optional PDF dependency; unavailable/invalid PDFs fail closed."""

    @staticmethod
    def reader(data: bytes):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise SessionReview("ENCRYPTED_COMPONENT")
        # Force page-tree and content readback, not just a header check.
        for page in reader.pages:
            contents = page.get_contents()
            if contents is not None:
                contents.get_data()
        return reader

    def count(self, data: bytes) -> int:
        return len(self.reader(data).pages)

    def assemble(self, pages: list[tuple[bytes, int]]) -> bytes:
        from pypdf import PdfWriter
        writer = PdfWriter()
        for data, index in pages:
            writer.add_page(self.reader(data).pages[index])
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".assembly-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ScanSessionAssembler:
    def __init__(self, state, output_directory: Path, *, codec=None):
        self.state = state
        self.output_directory = Path(output_directory)
        self.codec = codec or PdfCodec()

    def get(self, session_id: str) -> dict:
        with closing(sqlite3.connect(self.state.db_path)) as db:
            row = db.execute("SELECT payload FROM family_document_scan_sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise SessionReview("UNKNOWN_SESSION")
        return json.loads(row[0])

    def apply(self, event: dict) -> dict:
        """Persist an explicit event; return REVIEW without exposing incomplete output.

        Exact event replays are no-ops. A finalized session rejects all new or
        conflicting events without mutating its original receipt.
        """
        event = json.loads(canonical(event))
        for field in ("session_id", "scanner_id", "profile_id", "event_id"):
            if not isinstance(event.get(field), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", event[field]):
                raise SessionReview("INVALID_EXPLICIT_ID")
        with closing(sqlite3.connect(self.state.db_path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM family_document_scan_sessions WHERE session_id=?", (event["session_id"],)).fetchone()
            session = json.loads(row[0]) if row else {
                "session_id": event["session_id"], "scanner_id": event["scanner_id"],
                "profile_id": event["profile_id"], "status": "OPEN", "events": {}, "passes": [],
            }
            previous = session["events"].get(event["event_id"])
            if previous == event:
                return session
            if session["status"] == "FINALIZED":
                raise SessionReview("FINALIZED_SESSION_IMMUTABLE")
            try:
                if previous is not None:
                    raise SessionReview("CONFLICTING_EVENT_REPLAY")
                if any(session[key] != event[key] for key in ("scanner_id", "profile_id")):
                    raise SessionReview("SCANNER_PROFILE_MISMATCH")
                kind = event.get("kind")
                if kind == "start":
                    if "sheet_count" in session or type(event.get("sheet_count")) is not int or not 1 <= event["sheet_count"] <= 250:
                        raise SessionReview("INVALID_SESSION_START")
                    session["sheet_count"] = event["sheet_count"]
                elif kind == "pass":
                    if "sheet_count" not in session:
                        raise SessionReview("START_REQUIRED")
                    self._validate_pass(session, event)
                    session["passes"].append(event)
                elif kind == "timeout":
                    raise SessionReview("TIMEOUT_RECOVERABLE")
                elif kind == "complete":
                    if session.get("mapping_error"):
                        raise SessionReview("UNRESOLVED_MAPPING_ERROR")
                    receipt = self._assemble(session)
                    receipt["completion_event_id"] = event["event_id"]
                    receipt["idempotency_key"] = "scan:" + digest(session["session_id"].encode()) + ":" + receipt["sha256"]
                    session["receipt"] = receipt
                    session["status"] = "FINALIZED"
                else:
                    raise SessionReview("UNKNOWN_EVENT_KIND")
                if kind != "complete":
                    session["status"] = "REVIEW" if session.get("mapping_error") else "OPEN"
                if not session.get("mapping_error"):
                    session.pop("reason", None)
            except Exception as exc:
                session["status"] = "REVIEW"
                if isinstance(exc, SessionReview) and str(exc) in {"SCANNER_PROFILE_MISMATCH", "CONFLICTING_EVENT_REPLAY", "AMBIGUOUS_ORIENTATION", "AMBIGUOUS_PHYSICAL_SLOT", "DUPLICATE_PASS"}:
                    session["mapping_error"] = True
                session["reason"] = str(exc) if isinstance(exc, SessionReview) else "PDF_VERIFICATION_FAILED"
            if previous is None:
                session["events"][event["event_id"]] = event
            db.execute("INSERT INTO family_document_scan_sessions VALUES (?,?,?) ON CONFLICT(session_id) DO UPDATE SET status=excluded.status,payload=excluded.payload",
                       (session["session_id"], session["status"], canonical(session)))
        return session

    def _validate_pass(self, session: dict, event: dict) -> None:
        if not isinstance(event.get("pass_id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", event["pass_id"]):
            raise SessionReview("EXPLICIT_PASS_REQUIRED")
        if any(item["pass_id"] == event["pass_id"] for item in session["passes"]):
            raise SessionReview("DUPLICATE_PASS")
        if event.get("orientation") != "physical_slots" or event.get("side") not in ("front", "back"):
            raise SessionReview("AMBIGUOUS_ORIENTATION")
        pages = event.get("pages")
        if not isinstance(pages, list) or not pages:
            raise SessionReview("EXPLICIT_SLOTS_REQUIRED")
        occupied = {(p["sheet"], item["side"]) for item in session["passes"] for p in item["pages"]}
        components = {(p["path"], p["page_index"]) for item in session["passes"] for p in item["pages"] if p["kind"] != "absent"}
        for page in pages:
            sheet = page.get("sheet")
            slot = (sheet, event["side"])
            if type(sheet) is not int or not 1 <= sheet <= session["sheet_count"] or slot in occupied:
                raise SessionReview("AMBIGUOUS_PHYSICAL_SLOT")
            occupied.add(slot)
            if page.get("kind") == "absent":
                if event["side"] != "back" or set(page) != {"sheet", "kind"}:
                    raise SessionReview("INVALID_ABSENT_BACK")
                continue
            if page.get("kind") not in ("page", "blank") or type(page.get("page_index")) is not int or page["page_index"] < 0:
                raise SessionReview("INVALID_PAGE_INDEX")
            if not isinstance(page.get("path"), str) or not isinstance(page.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", page["sha256"]):
                raise SessionReview("COMPONENT_SNAPSHOT_REQUIRED")
            identity = (page["path"], page["page_index"])
            if identity in components:
                raise SessionReview("DUPLICATE_COMPONENT_PAGE")
            components.add(identity)

    def _assemble(self, session: dict) -> dict:
        count = session.get("sheet_count", 0)
        slots = {(p["sheet"], item["side"]): (item, p) for item in session["passes"] for p in item["pages"]}
        if not count or set(slots) != {(sheet, side) for sheet in range(1, count + 1) for side in ("front", "back")}:
            raise SessionReview("INCOMPLETE_PHYSICAL_SLOTS")
        snapshots = {}
        pages, provenance = [], []
        for sheet in range(1, count + 1):
            for side in ("front", "back"):
                item, page = slots[sheet, side]
                safe = {"sheet": sheet, "side": side, "kind": page["kind"], "pass_id": item["pass_id"], "event_id": item["event_id"]}
                if page["kind"] != "absent":
                    path = Path(page["path"])
                    if path.is_symlink():
                        raise SessionReview("UNSAFE_COMPONENT")
                    if path not in snapshots:
                        snapshots[path] = path.read_bytes()
                    data = snapshots[path]
                    if digest(data) != page["sha256"] or not 0 <= page["page_index"] < self.codec.count(data):
                        raise SessionReview("COMPONENT_SNAPSHOT_MISMATCH")
                    pages.append((data, page["page_index"]))
                    safe.update(page_index=page["page_index"], component_sha256=page["sha256"], output_page_index=len(pages) - 1)
                provenance.append(safe)
        result = self.codec.assemble(pages)
        if self.codec.count(result) != len(pages):
            raise SessionReview("ASSEMBLED_PAGE_COUNT_MISMATCH")
        # Component files must still match the captured snapshots before publication.
        if any(path.read_bytes() != data for path, data in snapshots.items()):
            raise SessionReview("COMPONENT_CHANGED_DURING_ASSEMBLY")
        name = "scan-" + digest(session["session_id"].encode()) + ".pdf"
        target = self.output_directory / name
        atomic_write(target, result)
        readback = target.read_bytes()
        try:
            if digest(readback) != digest(result) or self.codec.count(readback) != len(pages):
                raise SessionReview("FINAL_READBACK_FAILED")
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise SessionReview("FINAL_READBACK_FAILED") from exc
        return {"session_id": session["session_id"], "scanner_id": session["scanner_id"],
                "profile_id": session["profile_id"], "sha256": digest(readback),
                "page_count": len(pages), "slots": provenance, "output_name": name,
                "readback_verified": True}

    def receipt_for_path(self, path: Path) -> dict:
        with closing(sqlite3.connect(self.state.db_path)) as db:
            rows = db.execute("SELECT payload FROM family_document_scan_sessions WHERE status='FINALIZED'").fetchall()
        for row in rows:
            session = json.loads(row[0])
            if path.resolve() == (self.output_directory / session["receipt"]["output_name"]).resolve():
                _, receipt = self.verified_document(session["session_id"])
                return receipt
        raise SessionReview("UNFINALIZED_SOURCE")

    def verified_document(self, session_id: str):
        session = self.get(session_id)
        if session["status"] != "FINALIZED":
            raise SessionReview("EXPLICIT_COMPLETION_REQUIRED")
        receipt = session["receipt"]
        path = self.output_directory / receipt["output_name"]
        gate = StableFileGate()
        gate.observe(path)
        document = gate.observe(path)
        try:
            valid = document.stable and document.sha256 == receipt["sha256"] and self.codec.count(path.read_bytes()) == receipt["page_count"]
        except Exception as exc:
            raise SessionReview("FINAL_SOURCE_CHANGED") from exc
        if not valid:
            raise SessionReview("FINAL_SOURCE_CHANGED")
        return document, receipt

    def write_handoff(self, session_id: str, handoff_root: Path) -> Path:
        # Keep only bounded identifiers and output-page coordinates in the local inference packet.
        from core.family_document_intake import build_intake_request
        document, receipt = self.verified_document(session_id)
        request = build_intake_request(document.path, source_id=document.source_id, source_sha256=document.sha256, scan_provenance=receipt)
        if request.page_count != receipt["page_count"]:
            raise SessionReview("OCR_PAGE_COUNT_MISMATCH")
        provenance = safe_scan_provenance(receipt)
        key = "scan:" + digest(session_id.encode()) + ":" + document.sha256
        packet = {"schema": "skeleton.family_document_inference_handoff.v1", "idempotency_key": key,
                  "payload": {"ocr_text": request.ocr_text, "source_kind": "mfp", "page_count": request.page_count, "mime_type": request.mime_type, "scan_provenance": provenance}}
        name = digest(key.encode()) + ".json"
        for status in ("accepted", "processing", "review", "pending"):
            existing = Path(handoff_root) / status / name
            if existing.exists():
                if existing.read_bytes() != canonical(packet).encode():
                    raise SessionReview("HANDOFF_CONFLICT")
                return existing
        target = Path(handoff_root) / "pending" / name
        atomic_write(target, canonical(packet).encode())
        return target