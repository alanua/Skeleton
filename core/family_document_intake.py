from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from core.family_document_runtime import FamilyDocumentRuntime, atomic_bytes, read_json, sha256_bytes, utc_now
from core.family_document_sources import MfpSourceProfile
from core.local_document_ocr import recognize_local_page, synthetic_pdf_page_texts


DOCUMENT_RECORD_SCHEMA = "skeleton.family_document_record.v1"
PUBLIC_RECEIPT_SCHEMA = "skeleton.family_document_receipt.v1"
_PDF_ESCAPE_RE = re.compile(r"([\\()])")


def _escape_pdf_text(text: str) -> str:
    return _PDF_ESCAPE_RE.sub(r"\\\1", text)


def _minimal_pdf(page_labels: list[str]) -> bytes:
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(len(page_labels)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_labels)} >>".encode("ascii"))
    for index, label in enumerate(page_labels):
        page_obj = 3 + index * 2
        content_obj = page_obj + 1
        marker = f"%%SKELETON_PAGE:{label}\n"
        stream = (
            marker
            + "BT /F1 10 Tf 72 720 Td ("
            + _escape_pdf_text(label[:120])
            + ") Tj ET\n"
        ).encode("utf-8")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> "
            f"/Contents {content_obj} 0 R >>".encode("ascii")
        )
        objects.append(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream")
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


def _component_pages(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".pdf":
        labels = synthetic_pdf_page_texts(path)
    else:
        labels = [f"image:{path.suffix.lower()}:{hashlib.sha256(path.read_bytes()).hexdigest()[:12]}"]
    return [{"source_path": str(path), "page_index": index, "label": label} for index, label in enumerate(labels)]


def _public_component(component: dict[str, Any]) -> dict[str, object]:
    return {
        "component_id": component["component_id"],
        "sequence": component["sequence"],
        "page_count": len(component["pages"]),
        "source_sha256": component["source_sha256"],
    }


class MfpScanSessionAssembler:
    def __init__(self, runtime: FamilyDocumentRuntime) -> None:
        self.runtime = runtime

    def ingest(self, path: str | Path, profile: MfpSourceProfile, *, discovered_at: float) -> dict[str, object]:
        source = Path(path).expanduser().resolve(strict=False)
        if not source.is_file():
            raise FileNotFoundError(str(source))
        source_bytes = source.read_bytes()
        source_hash = sha256_bytes(source_bytes)
        component_id = self.runtime.component_id_for_path(source)
        existing_claim = read_json(self.runtime.claim_path(component_id))
        if existing_claim:
            return {
                "schema": PUBLIC_RECEIPT_SCHEMA,
                "status": "DUPLICATE",
                "component_count": 0,
                "document_count": 0,
                "component_id": component_id,
            }

        self.recover_stale_sessions(now=discovered_at)
        session = self._session_for(profile, discovered_at)
        component = {
            "component_id": component_id,
            "source_path": str(source),
            "source_sha256": source_hash,
            "sequence": self.runtime.next_sequence(),
            "discovered_at": discovered_at,
            "pages": _component_pages(source),
        }
        claim = {
            "schema": "skeleton.family_document_component_claim.v1",
            "component_id": component_id,
            "session_id": session["session_id"],
            "source_identity": profile.identity,
            "source_sha256": source_hash,
            "sequence": component["sequence"],
            "claimed_at": utc_now(),
        }
        self.runtime.write_claim(component_id, claim)
        session["components"].append(component)
        session["last_discovered_at"] = discovered_at
        self.runtime.write_session(session)
        return {
            "schema": PUBLIC_RECEIPT_SCHEMA,
            "status": "CLAIMED",
            "component_count": 1,
            "document_count": 0,
            "session_id": session["session_id"],
        }

    def recover_stale_sessions(self, *, now: float) -> list[dict[str, object]]:
        receipts = []
        for session in self.runtime.open_sessions():
            window = int(session.get("inactivity_window_seconds", 60))
            if now - float(session.get("last_discovered_at", session.get("opened_at", now))) >= window:
                receipts.append(self.finalize_session(str(session["session_id"])))
        return receipts

    def finalize_session(self, session_id: str) -> dict[str, object]:
        session = read_json(self.runtime.session_path(session_id))
        if not session:
            raise ValueError("unknown_session")
        if session.get("state") == "finalized":
            return dict(session["final_receipt"])
        components = sorted(session.get("components", []), key=lambda item: int(item["sequence"]))
        docs: list[list[dict[str, Any]]] = [[]]
        separators = 0
        review = False
        for component in components:
            for page in component["pages"]:
                recognition = recognize_local_page(page["source_path"], int(page["page_index"]))
                if recognition.ambiguous_separator:
                    review = True
                elif recognition.strict_separator:
                    separators += 1
                    if docs[-1]:
                        docs.append([])
                    continue
                docs[-1].append({**page, "component_id": component["component_id"], "sequence": component["sequence"]})
        docs = [doc for doc in docs if doc]
        if review:
            session["state"] = "review"
            receipt = {
                "schema": PUBLIC_RECEIPT_SCHEMA,
                "status": "REVIEW",
                "reason": "AMBIGUOUS_SEPARATOR",
                "component_count": len(components),
                "document_count": 0,
            }
            session["final_receipt"] = receipt
            self.runtime.write_session(session)
            return receipt

        records = []
        for index, pages in enumerate(docs, start=1):
            doc_seed = f"{session_id}:{index}:{','.join(str(page['sequence']) + ':' + str(page['page_index']) for page in pages)}"
            document_id = "famdoc_" + sha256_bytes(doc_seed.encode("utf-8"))[:24]
            output = self.runtime.assembly_path(document_id)
            if not output.is_file():
                labels = [str(page.get("label") or "") for page in pages]
                atomic_bytes(output, _minimal_pdf(labels))
            assembled_bytes = output.read_bytes()
            readback_page_count = len(synthetic_pdf_page_texts(output))
            assembled_hash = sha256_bytes(assembled_bytes)
            if readback_page_count != len(pages):
                raise ValueError("assembled_pdf_readback_page_count_mismatch")
            record = {
                "schema": DOCUMENT_RECORD_SCHEMA,
                "document_id": document_id,
                "session_id": session_id,
                "document_sequence": index,
                "source_identity": session["source_identity"],
                "assembled_pdf": str(output),
                "assembled_sha256": assembled_hash,
                "page_count": len(pages),
                "readback_verified_before_ocr": True,
                "separator_pages_removed": separators,
                "components": [_public_component(component) for component in components],
                "page_provenance": [
                    {
                        "component_id": page["component_id"],
                        "component_sequence": page["sequence"],
                        "component_page_index": page["page_index"],
                    }
                    for page in pages
                ],
            }
            existing = read_json(self.runtime.record_path(document_id))
            if existing:
                record = existing
            else:
                self.runtime.write_record(document_id, record)
            records.append(record)
        receipt = {
            "schema": PUBLIC_RECEIPT_SCHEMA,
            "status": "DONE",
            "component_count": len(components),
            "document_count": len(records),
            "total_output_pages": sum(int(record["page_count"]) for record in records),
            "separator_pages_removed": separators,
            "readback_verified_before_ocr": True,
            "assembled_hashes": [record["assembled_sha256"] for record in records],
        }
        session["state"] = "finalized"
        session["finalized_at"] = utc_now()
        session["final_receipt"] = receipt
        self.runtime.write_session(session)
        self.runtime.write_receipt(session_id, receipt)
        return receipt

    def _session_for(self, profile: MfpSourceProfile, discovered_at: float) -> dict[str, Any]:
        candidates = [
            session
            for session in self.runtime.open_sessions()
            if session.get("source_identity") == profile.identity
            and discovered_at - float(session.get("last_discovered_at", discovered_at)) < profile.inactivity_window_seconds
        ]
        if candidates:
            return sorted(candidates, key=lambda item: str(item["session_id"]))[-1]
        session_id = "mfpsess_" + sha256_bytes(f"{profile.identity}:{discovered_at}:{self.runtime.next_sequence()}".encode())[:24]
        session = {
            "schema": "skeleton.family_document_scan_session.v1",
            "session_id": session_id,
            "source_identity": profile.identity,
            "source": profile.to_public_mapping(),
            "inactivity_window_seconds": profile.inactivity_window_seconds,
            "opened_at": discovered_at,
            "last_discovered_at": discovered_at,
            "state": "open",
            "components": [],
        }
        self.runtime.write_session(session)
        return session
