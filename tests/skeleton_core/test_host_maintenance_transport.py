from __future__ import annotations

import json
from pathlib import Path

import yaml

from tools.skeleton_core.host_maintenance_transport import poll_once


def write_packet(transport_root: Path, name: str = "packet.yaml", **overrides: object) -> Path:
    packet = {
        "command": "worktree_audit",
        "repository": "alanua/Skeleton",
    }
    packet.update(overrides)
    packet_path = transport_root / "inbox" / name
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")
    return packet_path


def read_report(transport_root: Path) -> dict:
    return json.loads((transport_root / "host_maintenance_transport_report.json").read_text(encoding="utf-8"))


def test_empty_inbox_returns_no_op_and_writes_report(tmp_path: Path) -> None:
    transport_root = tmp_path / "transport"

    report = poll_once(transport_root, worktree_root=tmp_path / "worktrees" / "skeleton")

    assert report.status == "no-op"
    assert (transport_root / "inbox").is_dir()
    assert (transport_root / "done").is_dir()
    assert (transport_root / "failed").is_dir()
    assert read_report(transport_root) == {
        "status": "no-op",
        "reason": "empty inbox",
    }


def test_valid_dry_run_packet_moves_to_done_and_writes_report(tmp_path: Path) -> None:
    transport_root = tmp_path / "transport"
    worktree_root = tmp_path / "worktrees" / "skeleton"
    packet_path = write_packet(transport_root)

    report = poll_once(transport_root, worktree_root=worktree_root)

    done_packet = transport_root / "done" / packet_path.name
    assert report.status == "done"
    assert not packet_path.exists()
    assert done_packet.is_file()
    written_report = read_report(transport_root)
    assert written_report["status"] == "done"
    assert written_report["moved_to"] == str(done_packet)
    assert written_report["processor"]["status"] == "ok"
    assert written_report["processor"]["apply"] is False
    assert written_report["processor"]["command"] == "worktree_audit"
    assert written_report["processor"]["candidates"] == []


def test_blocked_packet_moves_to_failed_and_writes_report(tmp_path: Path) -> None:
    transport_root = tmp_path / "transport"
    packet_path = write_packet(transport_root, command="run_shell")

    report = poll_once(transport_root, worktree_root=tmp_path / "worktrees" / "skeleton")

    failed_packet = transport_root / "failed" / packet_path.name
    assert report.status == "failed"
    assert not packet_path.exists()
    assert failed_packet.is_file()
    written_report = read_report(transport_root)
    assert written_report["status"] == "failed"
    assert written_report["moved_to"] == str(failed_packet)
    assert written_report["processor"]["status"] == "blocked"
    assert "command is not allowlisted" in written_report["processor"]["blocked_reason"]


def test_malformed_packet_moves_to_failed_and_writes_report(tmp_path: Path) -> None:
    transport_root = tmp_path / "transport"
    packet_path = transport_root / "inbox" / "not-yaml.yaml"
    packet_path.parent.mkdir(parents=True)
    packet_path.write_text("command: [unterminated\n", encoding="utf-8")

    report = poll_once(transport_root, worktree_root=tmp_path / "worktrees" / "skeleton")

    failed_packet = transport_root / "failed" / packet_path.name
    assert report.status == "failed"
    assert not packet_path.exists()
    assert failed_packet.is_file()
    written_report = read_report(transport_root)
    assert written_report["status"] == "failed"
    assert written_report["moved_to"] == str(failed_packet)
    assert "ParserError" in written_report["reason"] or "ScannerError" in written_report["reason"]


def test_apply_packet_remains_guarded_by_host_maintenance_executor(tmp_path: Path) -> None:
    transport_root = tmp_path / "transport"
    worktree_root = tmp_path / "worktrees" / "skeleton"
    packet_path = write_packet(
        transport_root,
        command="worktree_quarantine_clean_stale",
        apply=True,
        candidates=[str(tmp_path / "outside" / "issue-999")],
    )

    report = poll_once(transport_root, worktree_root=worktree_root)

    failed_packet = transport_root / "failed" / packet_path.name
    assert report.status == "failed"
    assert failed_packet.is_file()
    written_report = read_report(transport_root)
    assert written_report["processor"]["status"] == "blocked"
    assert written_report["processor"]["apply"] is True
    assert "outside allowlist" in written_report["processor"]["blocked_reason"]
