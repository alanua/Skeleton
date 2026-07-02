from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from core.aufmass_engine import AufmassInput, Opening, Point, RoomInput, calculate_aufmass
from core.aufmass_exporter import aufmass_result_to_json_dict, aufmass_result_to_rows
from core.aufmass_memory_bridge import AufmassMemoryBridge
from core.private_memory_history import content_hash
from scripts import skeleton_local_ops as ops


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "skeleton_local_ops.py"


def packet(width: float = 4.0) -> dict[str, object]:
    return {
        "schema": "skeleton.aufmass.local_input.v1",
        "project_ref": "project-001",
        "unit": "m",
        "rooms": [
            {
                "room_id": "room-001",
                "calculation_status": "accepted_input",
                "polygon": [[0, 0], [width, 0], [width, 3], [0, 3]],
                "height_m": 2.5,
                "height_status": "confirmed",
                "openings": [
                    {
                        "opening_id": "door-001",
                        "width_m": 0.9,
                        "height_m": 2.0,
                        "count": 1,
                        "status": "estimated_review",
                    }
                ],
                "source_evidence_refs": ["evidence-001"],
            },
            {
                "room_id": "room-002",
                "calculation_status": "blocked",
                "blocker": "height_missing",
            },
        ],
    }


def calculated_record(raw_packet: dict[str, object]) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    calculation, blocked, normalized = ops.validate_aufmass(raw_packet)
    result = calculate_aufmass(calculation)
    rows = ops.review_rows(aufmass_result_to_rows(result))
    review = {
        "schema": "skeleton.aufmass.review_results.v1",
        "project_ref": calculation.project_id,
        "unit": "m",
        "status": "PARTIAL_WITH_BLOCKERS",
        "rooms": [row for row in rows if row["row_type"] == "room"],
        "summary": next(row for row in rows if row["row_type"] == "summary"),
        "blocked_rooms": blocked,
    }
    audit = {
        "schema": "skeleton.aufmass.audit.v1",
        "status": review["status"],
        "input_hash": content_hash(normalized),
        "accepted_room_count": len(result.rooms),
        "blocked_room_count": len(blocked),
        "output_hashes": {"aufmass_results.json": "0" * 64, "aufmass_results.csv": "1" * 64},
    }
    return normalized, review, audit, aufmass_result_to_json_dict(result)


def run_cli(memory_root: Path, *args: str, expected: int = 0) -> dict[str, object]:
    env = {**os.environ, "SKELETON_PRIVATE_MEMORY_ROOT": str(memory_root)}
    result = subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == expected, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_bridge_writes_to_private_memory_stack_and_rebuilds_indexes(tmp_path: Path) -> None:
    bridge = AufmassMemoryBridge(tmp_path / "stack")
    normalized, review, audit, raw = calculated_record(packet())

    first = bridge.write_calculation(
        project_ref="project-001",
        normalized_input=normalized,
        review=review,
        audit=audit,
        raw_result=raw,
        actor_ref="operator",
        reason_code="test_reason",
        approval_ref="operator_approved",
        transaction_ref="tx-001",
    )
    repeated = bridge.write_calculation(
        project_ref="project-001",
        normalized_input=normalized,
        review=review,
        audit=audit,
        raw_result=raw,
        actor_ref="operator",
        reason_code="test_reason",
        approval_ref="operator_approved",
        transaction_ref="tx-001",
    )

    status = bridge.stack.status()
    context = bridge.context(project_ref="project-001", query="height_missing evidence")
    exact = bridge.stack.get(namespace="aufmass", fact_id=f"calculation.project-001.{audit['input_hash'][:24]}")

    assert first["idempotent"] is False
    assert repeated["idempotent"] is True
    assert status["state"] == "READY"
    assert status["mempalace"]["item_count"] >= 7
    assert status["graphify"]["relationship_count"] >= 7
    assert exact["value"]["calculation_input_policy"] == "explicit_packet_only"
    assert exact["value"]["normalized_input"]["rooms"][0]["openings"][0]["status"] == "estimated_review"
    assert context["authoritative_for_calculation_inputs"] is False


def test_compare_reports_changed_rooms_and_repeated_blockers(tmp_path: Path) -> None:
    bridge = AufmassMemoryBridge(tmp_path / "stack")
    normalized, review, audit, raw = calculated_record(packet())
    bridge.write_calculation(
        project_ref="project-001",
        normalized_input=normalized,
        review=review,
        audit=audit,
        raw_result=raw,
        actor_ref="operator",
        reason_code="test_reason",
        approval_ref="operator_approved",
        transaction_ref="tx-001",
    )
    normalized2, review2, audit2, raw2 = calculated_record(packet(width=5.0))
    second = bridge.write_calculation(
        project_ref="project-001",
        normalized_input=normalized2,
        review=review2,
        audit=audit2,
        raw_result=raw2,
        actor_ref="operator",
        reason_code="test_reason",
        approval_ref="operator_approved",
        transaction_ref="tx-002",
    )

    compare = second["compare"]

    assert compare["changed_rooms"] == ["room-001"]
    assert {"room_id": "room-002", "reason": "height_missing"} in compare["repeated_blockers"]
    assert compare["changed_quantities"]


def test_cli_aufmass_memory_uses_stack_root_without_legacy_database(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory-stack"
    legacy_root = tmp_path / "legacy"
    input_path = tmp_path / "input.json"
    output_dir = tmp_path / "output"
    input_path.write_text(json.dumps(packet()), encoding="utf-8")

    result = run_cli(
        memory_root,
        "--private-root",
        str(legacy_root),
        "aufmass",
        "calculate",
        "--input",
        str(input_path),
        "--output-dir",
        str(output_dir),
        "--use-memory",
        "--write-memory",
        "--actor",
        "operator",
        "--reason",
        "test_reason",
        "--approval",
        "operator_approved",
        "--transaction",
        "cli-tx-001",
    )
    context = run_cli(memory_root, "aufmass", "memory-context", "--project-ref", "project-001")
    history = run_cli(memory_root, "aufmass", "history", "--project-ref", "project-001")

    assert result["memory_written"] is True
    assert result["memory_idempotent"] is False
    assert result["memory_context"]["calculation_input_policy"] == "explicit_packet_only"
    assert (memory_root / "canonical.sqlite").is_file()
    assert (memory_root / "graphify.index.json").is_file()
    assert not (legacy_root / "memory" / "canonical.sqlite").exists()
    assert context["bounded_context"]["source_evidence_refs"] == ["evidence-001"]
    assert history["calculation_count"] == 1


def test_cli_use_memory_without_write_is_read_only(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory-stack"
    input_path = tmp_path / "input.json"
    output_dir = tmp_path / "output"
    input_path.write_text(json.dumps(packet()), encoding="utf-8")

    result = run_cli(
        memory_root,
        "aufmass",
        "calculate",
        "--input",
        str(input_path),
        "--output-dir",
        str(output_dir),
        "--use-memory",
    )

    assert result["memory_written"] is False
    assert result["memory_context"]["authoritative_for_calculation_inputs"] is False
    assert not (memory_root / "canonical.sqlite").exists()
    assert not (memory_root / "graphify.index.json").exists()
