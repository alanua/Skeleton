from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from tools.skeleton_core.runner_inbox import process_runner_inbox


ROOT = Path(__file__).resolve().parents[2]
TARGET = "projects/skeleton/REVIEW_QUEUE.yaml"


def write_packet(tmp_path: Path, **overrides: object) -> Path:
    packet = {
        "type": "append_review_queue_entries",
        "target": TARGET,
        "entries": [valid_entry()],
    }
    packet.update(overrides)
    packet_path = tmp_path / "runner-inbox.yaml"
    packet_path.write_text(yaml.safe_dump(packet, sort_keys=False), encoding="utf-8")
    return packet_path


def valid_entry(**overrides: object) -> dict[str, str]:
    entry = {
        "id": "RQ-2099-01-01-001",
        "source_batch": "runner_inbox_test",
        "date": "2099-01-01",
        "classification": "REVIEW",
        "target_project": "skeleton",
        "summary": "Public-safe Runner inbox test entry.",
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


def queue(root: Path) -> dict:
    return yaml.safe_load((root / TARGET).read_text(encoding="utf-8"))


def test_valid_append_review_queue_entries_packet_appends_entries(tmp_path: Path) -> None:
    root = repo_copy(tmp_path)
    original_text = (root / TARGET).read_text(encoding="utf-8")
    original_entries = queue(root)["entries"]
    packet_path = write_packet(tmp_path)

    report = process_runner_inbox(packet_path, repo_root=root)

    assert report.status == "appended"
    assert report.appended_entries == 1
    written_text = (root / TARGET).read_text(encoding="utf-8")
    assert written_text.startswith(original_text)
    parsed = queue(root)
    assert len(parsed["entries"]) == len(original_entries) + 1
    assert parsed["entries"][: len(original_entries)] == original_entries
    assert parsed["entries"][-1]["id"] == "RQ-2099-01-01-001"


def test_invalid_packet_type_is_blocked(tmp_path: Path) -> None:
    root = repo_copy(tmp_path)
    original_text = (root / TARGET).read_text(encoding="utf-8")
    packet_path = write_packet(tmp_path, type="run_shell")

    report = process_runner_inbox(packet_path, repo_root=root)

    assert report.status == "blocked"
    assert "packet type is not allowlisted" in str(report.blocked_reason)
    assert (root / TARGET).read_text(encoding="utf-8") == original_text


def test_invalid_target_path_is_blocked(tmp_path: Path) -> None:
    root = repo_copy(tmp_path)
    original_text = (root / TARGET).read_text(encoding="utf-8")
    packet_path = write_packet(tmp_path, target="projects/skeleton/STATE.yaml")

    report = process_runner_inbox(packet_path, repo_root=root)

    assert report.status == "blocked"
    assert "target path is not allowlisted" in str(report.blocked_reason)
    assert (root / TARGET).read_text(encoding="utf-8") == original_text


def test_secret_like_content_is_blocked(tmp_path: Path) -> None:
    root = repo_copy(tmp_path)
    original_text = (root / TARGET).read_text(encoding="utf-8")
    packet_path = write_packet(
        tmp_path,
        entries=[valid_entry(summary="Contains token sk-test12345678901234567890")],
    )

    report = process_runner_inbox(packet_path, repo_root=root)

    assert report.status == "blocked"
    assert "secret-like" in str(report.blocked_reason)
    assert (root / TARGET).read_text(encoding="utf-8") == original_text


def test_rejects_status_outside_review_backlog_rejected(tmp_path: Path) -> None:
    root = repo_copy(tmp_path)
    packet_path = write_packet(tmp_path, entries=[valid_entry(status="CANON")])

    report = process_runner_inbox(packet_path, repo_root=root)

    assert report.status == "blocked"
    assert "status is not allowlisted" in str(report.blocked_reason)


def test_yaml_remains_valid_after_append(tmp_path: Path) -> None:
    root = repo_copy(tmp_path)
    packet_path = write_packet(
        tmp_path,
        entries=[
            valid_entry(
                summary="Public-safe text with colon: still valid YAML.",
                recommended_action="Keep as REVIEW/BACKLOG material only.",
            )
        ],
    )

    report = process_runner_inbox(packet_path, repo_root=root)

    assert report.status == "appended"
    parsed = queue(root)
    assert parsed["entries"][-1]["summary"] == "Public-safe text with colon: still valid YAML."


def test_public_safe_private_route_wording_is_allowed(tmp_path: Path) -> None:
    root = repo_copy(tmp_path)
    packet_path = write_packet(
        tmp_path,
        entries=[
            valid_entry(
                id="RQ-2099-01-01-002",
                summary="Private memory route is discussed as an architecture boundary, not private data.",
                existing_match="Related to private memory routing terminology.",
                risk="Could be misunderstood if treated as actual private content.",
                recommended_action="Keep as REVIEW terminology only.",
            )
        ],
    )

    report = process_runner_inbox(packet_path, repo_root=root)

    assert report.status == "appended"
    assert queue(root)["entries"][-1]["id"] == "RQ-2099-01-01-002"
