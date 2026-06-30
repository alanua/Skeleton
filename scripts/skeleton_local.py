#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import skeleton_local_ops as base
from core.aufmass_engine import calculate_aufmass
from core.aufmass_exporter import aufmass_result_to_json_dict, aufmass_result_to_rows


def calculate_repeatably(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    packet = base.read_json(args.input)
    calculation, blocked, normalized = base.validate_aufmass(packet)
    raw_result = calculate_aufmass(calculation)
    raw = aufmass_result_to_json_dict(raw_result)
    rows = base.review_rows(aufmass_result_to_rows(raw_result))
    review = {
        "schema": "skeleton.aufmass.review_results.v1",
        "project_ref": calculation.project_id,
        "unit": "m",
        "status": "PARTIAL_WITH_BLOCKERS" if blocked else "READY_FOR_OPERATOR_REVIEW",
        "rooms": [row for row in rows if row["row_type"] == "room"],
        "summary": next(row for row in rows if row["row_type"] == "summary"),
        "blocked_rooms": blocked,
    }

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    base.os.chmod(output_dir, 0o700)
    results_json = base.contained(output_dir, "aufmass_results.json")
    results_csv = base.contained(output_dir, "aufmass_results.csv")
    audit_json = base.contained(output_dir, "aufmass_audit.json")
    base.write_json(results_json, review)
    base.atomic_write(results_csv, base.csv_text(rows).encode())

    stable_hashes = {
        "aufmass_results.json": base.sha256_file(results_json),
        "aufmass_results.csv": base.sha256_file(results_csv),
    }
    audit = {
        "schema": "skeleton.aufmass.audit.v1",
        "status": review["status"],
        "created_at": base.datetime.now(base.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "input_hash": base.content_hash(normalized),
        "accepted_room_count": len(raw_result.rooms),
        "blocked_room_count": len(blocked),
        "engine": "core.aufmass_engine.calculate_aufmass",
        "raw_result": raw,
        "output_hashes": stable_hashes,
    }
    base.write_json(audit_json, audit)
    reported_hashes = {**stable_hashes, "aufmass_audit.json": base.sha256_file(audit_json)}

    memory_revision = None
    memory_idempotent = False
    if args.write_memory:
        if not all((args.actor, args.reason, args.approval, args.transaction)):
            raise base.LocalOpsError("memory metadata is required")
        memory_value = {
            "schema": "skeleton.aufmass.calculation_record.v1",
            "project_ref": calculation.project_id,
            "status": audit["status"],
            "input_hash": audit["input_hash"],
            "output_hashes": stable_hashes,
            "accepted_room_count": audit["accepted_room_count"],
            "blocked_room_count": audit["blocked_room_count"],
        }
        memory_args = argparse.Namespace(
            namespace="aufmass",
            fact_id=f"calculation.{calculation.project_id}",
            value_json=json.dumps(memory_value, sort_keys=True),
            actor=args.actor,
            reason=args.reason,
            approval=args.approval,
            transaction=args.transaction,
        )
        memory_result = base.memory_put(memory_args, root)
        memory_revision = memory_result["canonical_revision"]
        memory_idempotent = bool(memory_result["idempotent"])

    return base.done(
        "aufmass.calculate",
        calculation_status=audit["status"],
        accepted_room_count=audit["accepted_room_count"],
        blocked_room_count=audit["blocked_room_count"],
        input_hash=audit["input_hash"],
        output_hashes=reported_hashes,
        memory_written=memory_revision is not None,
        memory_revision=memory_revision,
        memory_idempotent=memory_idempotent,
    )


base.aufmass_calculate = calculate_repeatably

if __name__ == "__main__":
    raise SystemExit(base.main())
