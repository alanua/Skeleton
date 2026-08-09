from __future__ import annotations

import base64
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import pytest

from core.home_edge import media_source_snapshot as snapshot
from core.home_edge.executor import HomeEdgeExecReceipt, HomeEdgeExecRequest, sign_request


SHA = "a" * 40
SECRET = "test-home-edge-secret"
PRIVATE_MARKER = "10.44.55.66"


def issue_body(**updates: str) -> str:
    fields = {
        "Mode": "RUNTIME_MAINTENANCE_TASK",
        "Maintenance Task ID": snapshot.TASK_ID,
        "Repository": snapshot.REPOSITORY,
        "Expected Main SHA": SHA,
        "Target": snapshot.TARGET_NODE,
    }
    fields.update(updates)
    return "\n".join(f"{key}: {value}" for key, value in fields.items())


def valid_source(marker: str = PRIVATE_MARKER) -> bytes:
    return f'''from flask import Flask

app = Flask("skeleton-cast-media")
SKELETON_CAST_VERSION = "v63"
MEDIA_PRIVATE_RUNTIME = "{marker}"

@app.route("/video")
def video():
    return "skeleton cast media"

@app.route("/health")
def health():
    return {{"service": "skeleton-cast", "status": "ok"}}
'''.encode()


def executor_stdout(source: bytes) -> str:
    local = snapshot._validate_source_bytes(source)
    assert local["source_identity"] == "verified"
    public = dict(local)
    public["executor_receipt_hash"] = "pending"
    return json.dumps(
        {
            "public": public,
            "private_source_b64": base64.b64encode(source).decode("ascii"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def executor_receipt(stdout: str) -> HomeEdgeExecReceipt:
    now = datetime.now(UTC).isoformat()
    return HomeEdgeExecReceipt(
        status="ok",
        request_id="req",
        node_id=snapshot.TARGET_NODE,
        execution_lane=snapshot.EXECUTION_LANE,
        exit_code=0,
        stdout=stdout,
        stderr="",
        started_at=now,
        finished_at=now,
        duration_seconds=0.01,
        idempotency="executed",
        receipt_hash="f" * 64,
    )


def test_exact_fixed_task_path_node_lane_run_as_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(snapshot.EXEC_HMAC_SECRET_ENV, SECRET)

    request = snapshot.build_snapshot_request()

    assert snapshot.TASK_ID == "home_edge_01_media_source_snapshot_v1"
    assert snapshot.SOURCE_PATH == "/opt/skeleton/cast/app.py"
    assert snapshot.MAX_SOURCE_BYTES == 2 * 1024 * 1024
    assert snapshot.SOURCE_PATH in request.script
    assert request.node_id == "home-edge-01"
    assert request.execution_lane.value == "read_only"
    assert request.run_as.value == "desktop-user"
    assert request.timeout_seconds == 30
    assert request.argv == ()
    assert request.mode.value == "script"
    assert request.script_interpreter == "python3"
    assert request.signature == sign_request(request, SECRET)


@pytest.mark.parametrize(
    "body,reason",
    [
        (issue_body(**{"Expected Main SHA": "A" * 40}), "expected_main_sha_malformed"),
        (issue_body() + "\nExpected Main SHA: " + SHA, "duplicate_runtime_input_field"),
        (issue_body() + "\nPath: /tmp/evil.py", "unknown_runtime_input_field"),
        (issue_body() + "\nScript: print(1)", "unknown_runtime_input_field"),
        (issue_body() + "\nOutput Path: /tmp/out", "unknown_runtime_input_field"),
        (issue_body(Target="home-edge-02"), "target_mismatch"),
        (issue_body() + "\nTimeout: 1", "unknown_runtime_input_field"),
        (issue_body() + "\nLane: destructive", "unknown_runtime_input_field"),
        (issue_body() + "\nRun As: root", "unknown_runtime_input_field"),
    ],
)
def test_malformed_unknown_issue_fields_cannot_change_behavior(body: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        snapshot.parse_runtime_input(body)


def test_execute_uses_only_signed_executor_gateway_and_writes_private_0600(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[Mapping[str, Any]] = []
    source = valid_source()
    monkeypatch.setenv(snapshot.EXEC_HMAC_SECRET_ENV, SECRET)

    def fake_execute(request: Mapping[str, Any]) -> HomeEdgeExecReceipt:
        calls.append(request)
        return executor_receipt(executor_stdout(source))

    monkeypatch.setattr(snapshot, "execute_home_edge_request", fake_execute)

    receipt = snapshot.execute_media_source_snapshot_task(
        issue_body(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
        private_root=tmp_path,
    )

    assert snapshot.success_criteria_met(receipt)
    assert len(calls) == 1
    request = HomeEdgeExecRequest.from_mapping(calls[0])
    assert request.signature == sign_request(request, SECRET)
    assert request.node_id == snapshot.TARGET_NODE
    assert request.execution_lane.value == snapshot.EXECUTION_LANE
    assert request.run_as.value == snapshot.RUN_AS
    assert request.timeout_seconds == snapshot.REQUEST_TIMEOUT_SECONDS
    artifact = snapshot.private_artifact_path(private_root=tmp_path)
    assert artifact.read_bytes() == source
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    assert stat.S_IMODE(artifact.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "source,reason",
    [
        (b"def nope(:\n", "python_parse_failed"),
        (b"print('not skeleton cast media')\n", "video_route_missing"),
        (
            b"from flask import Flask\napp=Flask('skeleton-cast-media')\n@app.route('/video')\ndef video(): return 'x'\n",
            "health_route_missing",
        ),
        (
            b"from flask import Flask\napp=Flask('x')\n@app.route('/video')\ndef video(): return 'x'\n@app.route('/health')\ndef health(): return 'ok'\n",
            "skeleton_cast_markers_missing",
        ),
        (
            b"from flask import Flask\napp=Flask('skeleton-cast-media')\nAPI_KEY='super-secret-value'\n@app.route('/video')\ndef video(): return 'x'\n@app.route('/health')\ndef health(): return 'ok'\n",
            "suspicious_credential_literal",
        ),
    ],
)
def test_invalid_sources_block_before_artifact_publication(
    source: bytes,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(snapshot.EXEC_HMAC_SECRET_ENV, SECRET)
    blocked = snapshot._validate_source_bytes(source)

    def fake_execute(_request: Mapping[str, Any]) -> HomeEdgeExecReceipt:
        return executor_receipt(
            json.dumps(
                {
                    "public": blocked,
                    "private_source_b64": base64.b64encode(source).decode("ascii"),
                }
            )
        )

    monkeypatch.setattr(snapshot, "execute_home_edge_request", fake_execute)

    receipt = snapshot.execute_media_source_snapshot_task(
        issue_body(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
        private_root=tmp_path,
    )

    assert receipt["stable_reason"] == reason
    assert receipt["private_artifact_written"] is False
    assert not snapshot.private_artifact_path(private_root=tmp_path).exists()


def test_oversize_source_blocks_without_publication() -> None:
    receipt = snapshot._validate_source_bytes(b"x" * (snapshot.MAX_SOURCE_BYTES + 1))

    assert receipt["stable_reason"] == "source_oversize"
    assert receipt["source_identity"] == "blocked"


def test_remote_script_checks_file_safety_before_export() -> None:
    script = snapshot.SNAPSHOT_SCRIPT

    assert "os.lstat(SOURCE_PATH)" in script
    assert "stat.S_ISLNK" in script
    assert "stat.S_ISREG" in script
    assert "stat.S_IWOTH" in script
    assert "os.access(SOURCE_PATH, os.R_OK)" in script
    assert "base64.b64encode(source)" in script
    assert "private_source_b64" in script
    assert "ssh " not in script.lower()
    assert "sudo" not in script.lower()


def test_artifact_hash_size_mismatch_blocks_and_preserves_previous(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact = snapshot.private_artifact_path(private_root=tmp_path)
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"previous valid snapshot")
    os.chmod(artifact, 0o600)
    before = artifact.read_bytes()
    monkeypatch.setenv(snapshot.EXEC_HMAC_SECRET_ENV, SECRET)
    monkeypatch.setattr(snapshot, "_sha256_file", lambda _path: "0" * 64)
    monkeypatch.setattr(
        snapshot,
        "execute_home_edge_request",
        lambda _request: executor_receipt(executor_stdout(valid_source())),
    )

    receipt = snapshot.execute_media_source_snapshot_task(
        issue_body(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
        private_root=tmp_path,
    )

    assert receipt["stable_reason"] == "private_artifact_hash_mismatch"
    assert receipt["private_artifact_written"] is False
    assert artifact.read_bytes() == before


def test_public_receipt_and_status_lines_exclude_source_text_private_markers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = valid_source(marker=PRIVATE_MARKER)
    monkeypatch.setenv(snapshot.EXEC_HMAC_SECRET_ENV, SECRET)
    monkeypatch.setattr(
        snapshot,
        "execute_home_edge_request",
        lambda _request: executor_receipt(executor_stdout(source)),
    )

    receipt = snapshot.execute_media_source_snapshot_task(
        issue_body(),
        registered_clean_main_sha=SHA,
        github_main_sha=SHA,
        private_root=tmp_path,
    )
    public = json.dumps(receipt, sort_keys=True) + "\n".join(snapshot.receipt_status_lines(receipt))

    assert "private_source_b64" not in public
    assert "Flask" not in public
    assert "skeleton-cast-media" not in public
    assert PRIVATE_MARKER not in public
    assert snapshot.SOURCE_PATH not in public
    assert "source_sha256" in public
    assert "source_bytes" in public
