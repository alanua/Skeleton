#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.family_document_intake import DocumentProcessor, IntakeConfig, Person, public_receipt
from core.family_document_runtime import ProjectionOutbox, ReceiptOutbox
from core.family_document_sinks import CalendarSink, JsonCommandAdapter, MemoryGatewaySink
from core.family_document_sources import ApprovedRoot
from core.local_document_ocr import LocalDocumentOcr, OcrConfig


def load_processor(config_path: Path) -> DocumentProcessor:
    try:
        payload = json.loads(config_path.expanduser().resolve(strict=True).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("runtime_config_invalid") from exc
    if not isinstance(payload, Mapping):
        raise SystemExit("runtime_config_invalid")
    people = tuple(
        Person(str(item["person_id"]), tuple(str(alias) for alias in item["aliases"]))
        for item in payload["people"]
    )
    roots = tuple(
        ApprovedRoot(str(item["alias"]), Path(str(item["path"])))
        for item in payload["approved_roots"]
    )
    ocr = LocalDocumentOcr(
        OcrConfig(
            executables={str(key): str(value) for key, value in dict(payload["ocr_executables"]).items()},
            timeout_seconds=int(payload.get("ocr_timeout_seconds", 300)),
            max_output_bytes=int(payload.get("ocr_max_output_bytes", 2_000_000)),
            languages=tuple(str(value) for value in payload.get("ocr_languages", ("eng", "deu", "ukr"))),
        )
    )
    memory_adapter = JsonCommandAdapter(tuple(str(value) for value in payload["memory_command"]))
    calendar_adapter = JsonCommandAdapter(tuple(str(value) for value in payload["calendar_command"]))
    config = IntakeConfig(
        people=people,
        approved_roots=roots,
        archive_root=Path(str(payload["archive_root"])),
        memory_sink=MemoryGatewaySink(memory_adapter, approval_ref=str(payload["approval_ref"])),
            calendar_sink=CalendarSink(calendar_adapter),
            projection_outbox=ProjectionOutbox(Path(str(payload["projection_outbox_path"]))),
            receipt_outbox=ReceiptOutbox(Path(str(payload["receipt_outbox_path"]))),
            ocr=ocr,
            record_revision=str(payload.get("record_revision", "family-document-v1")),
        )
    return DocumentProcessor(config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="family-document-intake")
    parser.add_argument("action", choices=("plan", "process", "reconcile"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--packet", type=Path)
    args = parser.parse_args(argv)
    try:
        processor = load_processor(args.config)
        if args.action in {"plan", "process"}:
            if args.source is None:
                raise SystemExit("source_required")
            receipt = processor.process(args.source, dry_run=args.action == "plan")
        else:
            if args.packet is None:
                raise SystemExit("packet_path_required")
            packet, receipt = processor.reconcile()
            packet_path = args.packet.expanduser().resolve(strict=False)
            packet_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = packet_path.with_name(packet_path.name + ".part")
            temporary.write_text(json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(packet_path)
        print(json.dumps(receipt, ensure_ascii=False, allow_nan=False, sort_keys=True))
        return 0 if receipt.get("status") in {"DONE", "REVIEW"} else 2
    except SystemExit:
        raise
    except Exception:
        print(json.dumps(public_receipt("BLOCKED", "processing_failed", {}), sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
