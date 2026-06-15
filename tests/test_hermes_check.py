from __future__ import annotations

import json
import socket
from pathlib import Path
from unittest import mock

import pytest

from scripts import hermes_check


def valid_packet(**overrides: object) -> dict[str, object]:
    packet: dict[str, object] = {
        "schema": "hermes.task_packet.v0",
        "task_id": "SYNTH-MANUAL-CHECK-001",
        "title": "Synthetic manual Hermes check",
        "goal": "Validate a public-safe manual Hermes dry-run packet.",
        "worker_mode": "dry_run",
        "public_safe": True,
        "no_secrets": True,
        "no_runtime_mutation": True,
        "approval_required": True,
        "source_context": [
            {
                "source_type": "other_public_safe_source",
                "reference": "synthetic test packet",
                "public_safe": True,
                "read_only": True,
            }
        ],
        "scope": ["manual Hermes check"],
        "allowed_files": ["scripts/hermes_check.py", "tests/test_hermes_check.py"],
        "forbidden_actions": [
            "server_install",
            "runtime_service_change",
            "workflow_change",
            "private_data_access",
            "secret_access",
            "issue_mutation",
            "merge",
            "deploy",
            "publish",
        ],
        "validation": [
            {
                "command": "python3 -m pytest tests/test_hermes_check.py",
                "purpose": "Validate the manual Hermes check command.",
                "mutating": False,
            }
        ],
        "expected_outputs": ["public_safe_manual_check_result"],
        "authority_boundary": {
            "review_only": True,
            "mutation_allowed": False,
            "runtime_install_allowed": False,
            "approval_path": "authorized operator or reviewed process",
        },
    }
    packet.update(overrides)
    return packet


def write_packet(tmp_path: Path, packet: dict[str, object]) -> Path:
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    return path


def test_bundled_fixture_check_prints_ok(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = hermes_check.main([])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "HERMES_CHECK_RESULT=OK" in output
    assert "DECISION=allowed" in output
    assert "SYNTH-HERMES-DRY-RUN-001" not in output
    assert str(hermes_check.DEFAULT_TASK_PACKET) not in output


def test_blocked_input_prints_sanitized_blocked_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_path = "/private/tmp/customer/secret-token.txt"
    packet = valid_packet(worker_mode="live", no_runtime_mutation=False)
    packet["private_payload"] = {
        "token": "secret-value",
        "path": private_path,
        "hidden_text": "do-not-print",
    }
    packet_path = write_packet(tmp_path, packet)

    exit_code = hermes_check.main(["--packet", packet_path.as_posix(), "--no-skill"])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert output.splitlines() == [
        "HERMES_CHECK_RESULT=BLOCKED",
        "DECISION=not_allowed",
        "REASON=packet_requests_unsafe_or_live_execution",
        "WARNINGS=1",
    ]
    assert "secret-value" not in output
    assert "do-not-print" not in output
    assert private_path not in output
    assert packet_path.as_posix() not in output


def test_invalid_json_is_blocked_without_echoing_path_or_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    packet_path = tmp_path / "packet.json"
    packet_path.write_text('{"hidden_text": "do-not-print"', encoding="utf-8")

    exit_code = hermes_check.main(["--packet", packet_path.as_posix(), "--no-skill"])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert output.splitlines() == [
        "HERMES_CHECK_RESULT=BLOCKED",
        "DECISION=not_allowed",
        "REASON=input_json_invalid_or_unreadable",
        "WARNINGS=0",
    ]
    assert "do-not-print" not in output
    assert packet_path.as_posix() not in output


def test_manual_check_uses_read_only_file_modes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    packet_path = write_packet(tmp_path, valid_packet())
    opened_modes: list[str] = []
    original_open = Path.open

    def read_only_open(path: Path, mode: str = "r", *args: object, **kwargs: object):
        opened_modes.append(mode)
        assert "w" not in mode
        assert "a" not in mode
        assert "x" not in mode
        assert "+" not in mode
        return original_open(path, mode, *args, **kwargs)

    with mock.patch.object(Path, "open", read_only_open):
        exit_code = hermes_check.main(["--packet", packet_path.as_posix(), "--no-skill"])

    assert exit_code == 0
    assert opened_modes == ["r"]
    assert "HERMES_CHECK_RESULT=OK" in capsys.readouterr().out


def test_manual_check_does_not_need_network(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    packet_path = write_packet(tmp_path, valid_packet())

    with mock.patch.object(socket, "socket", side_effect=AssertionError("network")):
        exit_code = hermes_check.main(["--packet", packet_path.as_posix(), "--no-skill"])

    assert exit_code == 0
    assert "HERMES_CHECK_RESULT=OK" in capsys.readouterr().out
