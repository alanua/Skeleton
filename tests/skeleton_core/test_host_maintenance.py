from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml

from tools.skeleton_core.host_maintenance import process_host_maintenance


NOW = datetime(2026, 6, 7, tzinfo=UTC)
SKELETON_ORIGIN = "https://github.com/alanua/Skeleton.git"


def write_packet(tmp_path: Path, **overrides: object) -> Path:
    packet = {
        "command": "worktree_audit",
        "repository": "alanua/Skeleton",
    }
    packet.update(overrides)
    packet_path = tmp_path / "host-maintenance.yaml"
    packet_path.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")
    return packet_path


def git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def make_worktree(root: Path, name: str, *, origin: str = SKELETON_ORIGIN, dirty: bool = False) -> Path:
    path = root / name
    path.mkdir(parents=True)
    git(["init"], path)
    git(["config", "user.email", "runner@example.invalid"], path)
    git(["config", "user.name", "Runner Test"], path)
    (path / "README.md").write_text("test\n", encoding="utf-8")
    git(["add", "README.md"], path)
    git(["commit", "-m", "initial"], path)
    git(["remote", "add", "origin", origin], path)
    if dirty:
        (path / "README.md").write_text("dirty\n", encoding="utf-8")
    old = NOW.timestamp() - (30 * 24 * 60 * 60)
    os.utime(path, (old, old))
    return path


def read_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_audit_command_reports_candidates(tmp_path: Path) -> None:
    root = tmp_path / "worktrees" / "skeleton"
    candidate = make_worktree(root, "issue-831")
    packet_path = write_packet(tmp_path)
    report_path = tmp_path / "report.json"

    report = process_host_maintenance(packet_path, report_path=report_path, worktree_root=root, now=NOW)

    assert report.status == "ok"
    assert report.command == "worktree_audit"
    assert report.candidates == [
        {
            "path": str(candidate),
            "name": "issue-831",
            "eligible": True,
            "origin": SKELETON_ORIGIN,
            "dirty": False,
            "stale": True,
        }
    ]
    assert read_report(report_path)["candidates"][0]["name"] == "issue-831"


def test_dry_run_quarantine_reports_planned_actions_only(tmp_path: Path) -> None:
    root = tmp_path / "worktrees" / "skeleton"
    candidate = make_worktree(root, "issue-832")
    packet_path = write_packet(
        tmp_path,
        command="worktree_quarantine_clean_stale",
        candidates=[str(candidate)],
    )

    report = process_host_maintenance(packet_path, report_path=tmp_path / "report.json", worktree_root=root, now=NOW)

    assert report.apply is False
    assert report.actions == [
        {
            "action": "quarantine",
            "source": str(candidate),
            "destination": str(root / ".quarantine" / "issue-832"),
            "status": "planned",
        }
    ]
    assert candidate.is_dir()
    assert not (root / ".quarantine" / "issue-832").exists()


def test_apply_quarantine_moves_clean_stale_candidate_to_quarantine(tmp_path: Path) -> None:
    root = tmp_path / "worktrees" / "skeleton"
    candidate = make_worktree(root, "issue-833")
    packet_path = write_packet(
        tmp_path,
        command="worktree_quarantine_clean_stale",
        apply=True,
        candidates=[str(candidate)],
    )

    report = process_host_maintenance(packet_path, report_path=tmp_path / "report.json", worktree_root=root, now=NOW)

    destination = root / ".quarantine" / "issue-833"
    assert report.actions[0]["status"] == "applied"
    assert not candidate.exists()
    assert destination.is_dir()
    assert (destination / ".git").exists()


def test_dirty_candidate_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "worktrees" / "skeleton"
    candidate = make_worktree(root, "issue-834", dirty=True)
    packet_path = write_packet(
        tmp_path,
        command="worktree_quarantine_clean_stale",
        apply=True,
        candidates=[str(candidate)],
    )

    report = process_host_maintenance(packet_path, report_path=tmp_path / "report.json", worktree_root=root, now=NOW)

    assert report.actions == []
    assert report.candidates[0]["skip_reason"] == "dirty"
    assert candidate.is_dir()


def test_wrong_remote_candidate_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "worktrees" / "skeleton"
    candidate = make_worktree(root, "issue-835", origin="https://github.com/alanua/Other.git")
    packet_path = write_packet(
        tmp_path,
        command="worktree_quarantine_clean_stale",
        apply=True,
        candidates=[str(candidate)],
    )

    report = process_host_maintenance(packet_path, report_path=tmp_path / "report.json", worktree_root=root, now=NOW)

    assert report.actions == []
    assert report.candidates[0]["skip_reason"] == "wrong_remote"
    assert candidate.is_dir()


def test_path_outside_allowlist_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "worktrees" / "skeleton"
    packet_path = write_packet(
        tmp_path,
        command="worktree_quarantine_clean_stale",
        candidates=[str(tmp_path / "outside" / "issue-999")],
    )

    report = process_host_maintenance(packet_path, report_path=tmp_path / "report.json", worktree_root=root, now=NOW)

    assert report.status == "blocked"
    assert "outside allowlist" in str(report.blocked_reason)


def test_unknown_command_is_rejected(tmp_path: Path) -> None:
    packet_path = write_packet(tmp_path, command="run_shell")

    report = process_host_maintenance(
        packet_path,
        report_path=tmp_path / "report.json",
        worktree_root=tmp_path / "worktrees" / "skeleton",
        now=NOW,
    )

    assert report.status == "blocked"
    assert "command is not allowlisted" in str(report.blocked_reason)


def test_secret_like_packet_content_is_rejected(tmp_path: Path) -> None:
    packet_path = write_packet(tmp_path, candidates=["issue-secret"])

    report = process_host_maintenance(
        packet_path,
        report_path=tmp_path / "report.json",
        worktree_root=tmp_path / "worktrees" / "skeleton",
        now=NOW,
    )

    assert report.status == "blocked"
    assert "secret-like" in str(report.blocked_reason)


def test_validate_pr_branch_path_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "worktrees" / "skeleton"
    packet_path = write_packet(
        tmp_path,
        command="worktree_quarantine_clean_stale",
        candidates=["validate-pr-branch/pr-123"],
    )

    report = process_host_maintenance(packet_path, report_path=tmp_path / "report.json", worktree_root=root, now=NOW)

    assert report.status == "blocked"
    assert "not an issue worktree" in str(report.blocked_reason)


def test_poller_status_is_bounded_read_only_report(tmp_path: Path) -> None:
    packet_path = write_packet(tmp_path, command="poller_status", apply=True)
    report_path = tmp_path / "report.json"

    report = process_host_maintenance(
        packet_path,
        report_path=report_path,
        worktree_root=tmp_path / "worktrees" / "skeleton",
        now=NOW,
    )

    assert report.status == "ok"
    assert report.actions == [{"action": "poller_status", "status": "not_configured"}]
    assert read_report(report_path)["actions"] == [{"action": "poller_status", "status": "not_configured"}]
