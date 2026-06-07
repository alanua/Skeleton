from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from tools.skeleton_core.runner_inbox_transport import poll_once


ROOT = Path(__file__).resolve().parents[2]
TARGET = "projects/skeleton/REVIEW_QUEUE.yaml"


def valid_entry(**overrides: object) -> dict[str, str]:
    entry = {
        "id": "RQ-2099-01-01-101",
        "source_batch": "runner_inbox_transport_test",
        "date": "2099-01-01",
        "classification": "REVIEW",
        "target_project": "skeleton",
        "summary": "Public-safe Runner inbox transport test entry.",
        "existing_match": "No existing canonical behavior is changed.",
        "risk": "Could be mistaken for canon if reviewed outside the queue.",
        "recommended_action": "Keep in REVIEW until explicit operator approval.",
        "status": "REVIEW",
        "canon_status": "not_canon_until_promoted",
    }
    entry.update(overrides)
    return entry


def repo_copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    target_path = root / TARGET
    target_path.parent.mkdir(parents=True)
    shutil.copy2(ROOT / TARGET, target_path)
    return root


def write_packet(transport_root: Path, name: str = "packet.yaml", **overrides: object) -> Path:
    packet = {
        "type": "append_review_queue_entries",
        "target": TARGET,
        "entries": [valid_entry()],
    }
    packet.update(overrides)
    packet_path = transport_root / "inbox" / name
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")
    return packet_path


def queue(root: Path) -> dict:
    return yaml.safe_load((root / TARGET).read_text(encoding="utf-8"))


def read_report(transport_root: Path) -> dict:
    return json.loads((transport_root / "runner_inbox_report.json").read_text(encoding="utf-8"))


def test_valid_packet_moves_to_done_and_writes_report(tmp_path: Path) -> None:
    root = repo_copy(tmp_path)
    transport_root = tmp_path / "transport"
    packet_path = write_packet(transport_root)

    report = poll_once(transport_root, repo_root=root)

    done_packet = transport_root / "done" / packet_path.name
    assert report.status == "done"
    assert not packet_path.exists()
    assert done_packet.is_file()
    assert queue(root)["entries"][-1]["id"] == "RQ-2099-01-01-101"
    written_report = read_report(transport_root)
    assert written_report["status"] == "done"
    assert written_report["moved_to"] == str(done_packet)
    assert written_report["processor"]["status"] == "appended"
    assert written_report["processor"]["appended_entries"] == 1


def test_blocked_packet_moves_to_failed_and_writes_report(tmp_path: Path) -> None:
    root = repo_copy(tmp_path)
    original_text = (root / TARGET).read_text(encoding="utf-8")
    transport_root = tmp_path / "transport"
    packet_path = write_packet(transport_root, type="run_shell")

    report = poll_once(transport_root, repo_root=root)

    failed_packet = transport_root / "failed" / packet_path.name
    assert report.status == "failed"
    assert not packet_path.exists()
    assert failed_packet.is_file()
    assert (root / TARGET).read_text(encoding="utf-8") == original_text
    written_report = read_report(transport_root)
    assert written_report["status"] == "failed"
    assert written_report["moved_to"] == str(failed_packet)
    assert written_report["processor"]["status"] == "blocked"
    assert "packet type is not allowlisted" in written_report["processor"]["blocked_reason"]


def test_empty_inbox_returns_no_op_and_writes_report(tmp_path: Path) -> None:
    transport_root = tmp_path / "transport"

    report = poll_once(transport_root, repo_root=tmp_path / "repo")

    assert report.status == "no-op"
    assert (transport_root / "inbox").is_dir()
    assert (transport_root / "done").is_dir()
    assert (transport_root / "failed").is_dir()
    written_report = read_report(transport_root)
    assert written_report == {
        "status": "no-op",
        "reason": "empty inbox",
    }


def test_failed_packet_moves_to_failed_and_writes_report(tmp_path: Path) -> None:
    transport_root = tmp_path / "transport"
    packet_path = transport_root / "inbox" / "not-yaml.yaml"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text("type: [unterminated\n", encoding="utf-8")

    report = poll_once(transport_root, repo_root=tmp_path / "repo")

    failed_packet = transport_root / "failed" / packet_path.name
    assert report.status == "failed"
    assert not packet_path.exists()
    assert failed_packet.is_file()
    written_report = read_report(transport_root)
    assert written_report["status"] == "failed"
    assert written_report["moved_to"] == str(failed_packet)
    assert "ParserError" in written_report["reason"] or "ScannerError" in written_report["reason"]
