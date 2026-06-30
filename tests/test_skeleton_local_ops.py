from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "skeleton_local_ops.py"


def run_cli(private_root: Path, *args: str, expected: int = 0) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, str(CLI), "--private-root", str(private_root), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == expected, result.stderr or result.stdout
    return json.loads(result.stdout)


def mutation_args(transaction: str) -> list[str]:
    return [
        "--actor", "operator",
        "--reason", "test_reason",
        "--approval", "operator_approved",
        "--transaction", transaction,
    ]


def test_memory_roundtrip_idempotency_backup_and_restore(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    assert run_cli(private_root, "memory", "init")["status"] == "DONE"

    first = run_cli(
        private_root,
        "memory", "put",
        "--namespace", "project",
        "--fact-id", "decision-001",
        "--value-json", '{"approved":true,"value":42}',
        *mutation_args("put-001"),
    )
    assert first["idempotent"] is False

    repeated = run_cli(
        private_root,
        "memory", "put",
        "--namespace", "project",
        "--fact-id", "decision-001",
        "--value-json", '{"approved":true,"value":42}',
        *mutation_args("put-001"),
    )
    assert repeated["idempotent"] is True

    hidden = run_cli(
        private_root,
        "memory", "get",
        "--namespace", "project",
        "--fact-id", "decision-001",
    )
    assert hidden["found"] is True
    assert "value" not in hidden

    visible = run_cli(
        private_root,
        "memory", "get",
        "--namespace", "project",
        "--fact-id", "decision-001",
        "--show-value",
    )
    assert visible["value"] == {"approved": True, "value": 42}

    conflict = run_cli(
        private_root,
        "memory", "put",
        "--namespace", "project",
        "--fact-id", "decision-001",
        "--value-json", '{"approved":false}',
        *mutation_args("put-001"),
        expected=2,
    )
    assert conflict["status"] == "BLOCKED"

    backup = run_cli(private_root, "memory", "backup")
    manifest = private_root / "memory" / "manifests" / f"{backup['snapshot_id']}.json"
    verified = run_cli(private_root, "memory", "verify-backup", "--manifest", str(manifest))
    assert verified["status"] == "DONE"

    restored_root = tmp_path / "restored"
    restored = run_cli(
        private_root,
        "memory", "restore",
        "--manifest", str(manifest),
        "--target-root", str(restored_root),
    )
    assert restored["status"] == "DONE"
    assert restored["activated"] is False
    assert (restored_root / "memory" / "canonical.sqlite").is_file()


def test_aufmass_calculation_and_memory_record(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    run_cli(private_root, "memory", "init")
    input_path = tmp_path / "input.json"
    output_dir = tmp_path / "output"

    packet = {
        "schema": "skeleton.aufmass.local_input.v1",
        "project_ref": "project-001",
        "unit": "m",
        "rooms": [
            {
                "room_id": "room-001",
                "calculation_status": "accepted_input",
                "polygon": [[0, 0], [4, 0], [4, 3], [0, 3]],
                "height_m": 2.5,
                "height_status": "confirmed",
                "openings": [
                    {
                        "opening_id": "door-001",
                        "width_m": 0.9,
                        "height_m": 2.0,
                        "count": 1,
                        "status": "confirmed",
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
    input_path.write_text(json.dumps(packet), encoding="utf-8")

    validated = run_cli(private_root, "aufmass", "validate", "--input", str(input_path))
    assert validated["accepted_room_count"] == 1
    assert validated["blocked_room_count"] == 1

    calculated = run_cli(
        private_root,
        "aufmass", "calculate",
        "--input", str(input_path),
        "--output-dir", str(output_dir),
        "--write-memory",
        *mutation_args("aufmass-001"),
    )
    assert calculated["calculation_status"] == "PARTIAL_WITH_BLOCKERS"
    assert calculated["memory_written"] is True

    results = json.loads((output_dir / "aufmass_results.json").read_text(encoding="utf-8"))
    assert results["summary"]["floor_area"] == 12.0
    assert results["summary"]["perimeter"] == 14.0
    assert results["summary"]["gross_wall_area"] == 35.0
    assert results["summary"]["openings_area"] == 1.8
    assert results["summary"]["net_wall_area"] == 33.2
    assert results["summary"]["volume"] == 30.0
    assert results["blocked_rooms"] == [{"room_id": "room-002", "reason": "height_missing"}]
    assert (output_dir / "aufmass_results.csv").is_file()
    assert (output_dir / "aufmass_audit.json").is_file()

    memory_record = run_cli(
        private_root,
        "memory", "get",
        "--namespace", "aufmass",
        "--fact-id", "calculation.project-001",
        "--show-value",
    )
    assert memory_record["value"]["status"] == "PARTIAL_WITH_BLOCKERS"
    assert memory_record["value"]["accepted_room_count"] == 1
    assert memory_record["value"]["blocked_room_count"] == 1
