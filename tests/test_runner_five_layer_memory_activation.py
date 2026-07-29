from __future__ import annotations

# Synchronize protected Runner validation for canonical-root activation fallback.

import json
from pathlib import Path
from typing import Mapping

from core.private_memory import PRIVATE_MEMORY_CONFIG_ENV
from core.private_memory_stack import PrivateMemoryStack
from core.runner_five_layer_memory_activation import (
    OPERATOR_APPROVAL,
    TASK_ID,
    VIDEO_RUNTIME_APPROVAL,
    execute_five_layer_memory_activation,
)
from core.video_understanding.models import VideoUnderstandingError

SHA = "a" * 40


def _body(
    *,
    sha: str = SHA,
    approval: str = OPERATOR_APPROVAL,
    launch_video: bool | None = None,
    video_approval: str = VIDEO_RUNTIME_APPROVAL,
) -> str:
    body = (
        "Mode: RUNTIME_MAINTENANCE_TASK\n"
        f"Maintenance Task ID: {TASK_ID}\n"
        f"Expected Main SHA: {sha}\n"
        f"Operator Approval: {approval}\n"
    )
    if launch_video is not None:
        body += f"Launch Video Understanding: {str(launch_video).lower()}\n"
        body += f"Video Runtime Approval: {video_approval}\n"
    return body


def _report(
    status: str, task_id: str, lines: list[str], success: str
) -> str:
    return "\n".join(
        [
            f"{status}: Runner host maintenance task completed.",
            f"maintenance_task_id={task_id}",
            *lines,
            f"success_criteria={success}",
        ]
    )


def _receipt(*, status: str = "DONE") -> str:
    booleans = {
        "gateway_canonical": True,
        "projection_queue": True,
        "cognee_selected": True,
        "mempalace_fallback": True,
        "graphify_fresh": True,
        "project_isolation": True,
        "revision_invalidation": True,
        "mandatory_bootstrap": True,
        "handoff_cleanup": True,
        "private_echo_blocked": True,
        "forget_verified": True,
        "live_status_checked": True,
        "private_leak_detected": False,
    }
    return json.dumps(
        {
            "schema": "skeleton.five_layer_memory_activation_receipt.v2",
            "status": status,
            "reason_codes": [status],
            "source_sha": SHA,
            "booleans": booleans,
            "aggregate_counts": {
                "canonical_count": 1,
                "semantic_count": 1,
                "graph_count": 1,
                "outbox_done_count": 1,
            },
            "resource_totals": {
                "elapsed_ms": 100,
                "disk_bytes": 2048,
                "peak_rss_bytes": 4096,
            },
            "rollback": {"verified": True, "status": "verified"},
        },
        sort_keys=True,
    )


def _runner(
    command: list[str],
    cwd: Path,
    env: Mapping[str, str] | None,
    timeout: int,
) -> tuple[int, str, str]:
    del cwd, timeout
    if command[:3] == ["git", "rev-parse", "HEAD"]:
        return 0, SHA + "\n", ""
    if command[:3] == ["git", "branch", "--show-current"]:
        return 0, "main\n", ""
    if command[:3] == ["git", "status", "--porcelain"]:
        return 0, "", ""
    if command[:4] == ["git", "remote", "get-url", "origin"]:
        return 0, "https://github.com/alanua/Skeleton.git\n", ""
    if command == ["ollama", "list"]:
        return (
            0,
            "NAME ID SIZE MODIFIED\nqwen2.5:3b id 2GB now\n"
            "nomic-embed-text:latest id 1GB now\n",
            "",
        )
    assert command[:3] == [
        command[0],
        "-m",
        "scripts.activate_five_layer_private_memory",
    ]
    assert env is not None
    assert env["SKELETON_COGNEE_LLM_MODEL"] == "qwen2.5:3b"
    assert env["SKELETON_COGNEE_LLM_ENDPOINT"] == "http://127.0.0.1:11434/v1"
    assert env["SKELETON_COGNEE_EMBEDDING_MODEL"] == "nomic-embed-text:latest"
    assert env["SKELETON_COGNEE_EMBEDDING_ENDPOINT"] == "http://127.0.0.1:11434/api/embed"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "OPENAI_API_KEY" not in env
    return 0, _receipt() + "\n", ""


def _prepare_explicit_private_root(
    monkeypatch, tmp_path: Path, *, initialize: bool = True
) -> Path:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    private_root = tmp_path / "private"
    private_root.mkdir()
    if initialize:
        status = PrivateMemoryStack(private_root).init(import_manifest=True)
        assert status["state"] == "READY"
    monkeypatch.setenv("SKELETON_RUNNER_PRIVATE_MEMORY_ROOT", str(private_root))
    return checkout


def _prepare_legacy_configured_private_root(monkeypatch, tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    private_root = tmp_path / "private"
    private_root.mkdir()
    legacy_database = private_root / "legacy-heartbeat.sqlite"
    legacy_database.write_bytes(b"legacy anchor")
    config = tmp_path / "private-memory.json"
    config.write_text(
        json.dumps(
            {
                "schema": "skeleton.private_memory.config.v0",
                "database": {"path": str(legacy_database)},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(PRIVATE_MEMORY_CONFIG_ENV, str(config))
    return checkout


class _RuntimeResult:
    def __init__(self, **overrides: object) -> None:
        self.payload: dict[str, object] = {
            "schema": "skeleton.video_understanding.install_receipt.v1",
            "source_merge_sha": SHA,
            "runtime_config_status": "READY",
            "provider_ready_count": 4,
            "provider_required_count": 4,
            "ollama_status": "READY",
            "sona_status": "BLOCKED",
            "artifact_store_status": "READY",
            "queue_recovery_status": "DONE",
            "memory_gateway_status": "READY",
            "memory_roundtrip_status": "DONE",
            "service_install_status": "ACTIVE",
            "service_active": True,
            "worker_count": 1,
            "rollback_ready": True,
            "stable_reason_codes": ["ASR_FALLBACK_BLOCKED"],
        }
        self.payload.update(overrides)

    def public_dict(self) -> dict[str, object]:
        return dict(self.payload)


def test_activation_executor_returns_public_safe_done(monkeypatch, tmp_path: Path) -> None:
    checkout = _prepare_explicit_private_root(monkeypatch, tmp_path)
    report = execute_five_layer_memory_activation(
        _body(),
        workdir=checkout,
        maintenance_report=_report,
        command_runner=_runner,
    )
    assert report.startswith("DONE:")
    assert "head_sha=" + SHA in report
    assert "runtime_smoke_check_count=12" in report
    assert "disk_bytes=2048" in report
    assert "install_video_understanding" not in report
    assert str(tmp_path) not in report
    assert "success_criteria=met" in report


def test_activation_executor_launches_video_runtime_after_memory(
    monkeypatch, tmp_path: Path
) -> None:
    checkout = _prepare_explicit_private_root(monkeypatch, tmp_path)
    called: dict[str, object] = {}

    def installer(
        source_root: Path,
        *,
        expected_sha: str,
        enable: bool,
        env: Mapping[str, str],
    ) -> _RuntimeResult:
        called.update(
            {
                "source_root": source_root,
                "expected_sha": expected_sha,
                "enable": enable,
                "memory_root_present": bool(env.get("SKELETON_PRIVATE_MEMORY_ROOT")),
            }
        )
        return _RuntimeResult()

    report = execute_five_layer_memory_activation(
        _body(launch_video=True),
        workdir=checkout,
        maintenance_report=_report,
        command_runner=_runner,
        runtime_installer=installer,
    )
    assert report.startswith("DONE:")
    assert called == {
        "source_root": checkout.resolve(),
        "expected_sha": SHA,
        "enable": True,
        "memory_root_present": True,
    }
    assert "step=install_video_understanding status=done" in report
    assert "step=ensure_canonical_memory status=done" in report
    assert "canonical_memory_initialized=false" in report
    assert "canonical_memory_state=READY" in report
    assert "video_provider_ready_count=4" in report
    assert "video_ollama_status=READY" in report
    assert "video_sona_status=BLOCKED" in report
    assert "video_memory_roundtrip_status=DONE" in report
    assert "video_service_active=true" in report
    assert "video_worker_count=1" in report
    assert "video_rollback_ready=true" in report
    assert "video_stable_reason_codes=ASR_FALLBACK_BLOCKED" in report
    assert str(tmp_path) not in report


def test_activation_executor_initializes_missing_canonical_stack_before_video_installer(
    monkeypatch, tmp_path: Path
) -> None:
    checkout = _prepare_legacy_configured_private_root(monkeypatch, tmp_path)
    private_root = tmp_path / "private"
    called: dict[str, object] = {}

    def installer(
        source_root: Path,
        *,
        expected_sha: str,
        enable: bool,
        env: Mapping[str, str],
    ) -> _RuntimeResult:
        del source_root, expected_sha, enable, env
        status = PrivateMemoryStack(private_root).status()
        called["canonical_exists"] = (private_root / "canonical.sqlite").is_file()
        called["canonical_state"] = status["state"]
        called["canonical_sqlite_state"] = status["canonical_sqlite"]["state"]
        called["mempalace_state"] = status["mempalace"]["state"]
        called["graphify_state"] = status["graphify"]["state"]
        return _RuntimeResult()

    report = execute_five_layer_memory_activation(
        _body(launch_video=True),
        workdir=checkout,
        maintenance_report=_report,
        command_runner=_runner,
        runtime_installer=installer,
    )

    assert report.startswith("DONE:")
    assert called == {
        "canonical_exists": True,
        "canonical_state": "READY",
        "canonical_sqlite_state": "READY",
        "mempalace_state": "READY",
        "graphify_state": "READY",
    }
    assert "step=ensure_canonical_memory status=done" in report
    assert "canonical_memory_initialized=true" in report
    assert "canonical_memory_state=READY" in report
    assert "step=install_video_understanding status=done" in report
    assert str(tmp_path) not in report


def test_activation_executor_reuses_ready_existing_canonical_stack_without_reinit(
    monkeypatch, tmp_path: Path
) -> None:
    checkout = _prepare_explicit_private_root(monkeypatch, tmp_path)

    def fail_reinit(self, *, import_manifest: bool = True):
        del self, import_manifest
        raise AssertionError("existing canonical stack must not be reinitialized")

    monkeypatch.setattr(PrivateMemoryStack, "init", fail_reinit)

    report = execute_five_layer_memory_activation(
        _body(launch_video=True),
        workdir=checkout,
        maintenance_report=_report,
        command_runner=_runner,
        runtime_installer=lambda *args, **kwargs: _RuntimeResult(),
    )

    assert report.startswith("DONE:")
    assert "canonical_memory_initialized=false" in report
    assert "canonical_memory_state=READY" in report
    assert "step=install_video_understanding status=done" in report


def test_activation_executor_blocks_corrupt_existing_canonical_stack_before_installer(
    monkeypatch, tmp_path: Path
) -> None:
    checkout = _prepare_explicit_private_root(
        monkeypatch, tmp_path, initialize=False
    )
    private_root = tmp_path / "private"
    (private_root / "canonical.sqlite").write_bytes(b"not a sqlite database")
    called = False

    def installer(*args, **kwargs):
        nonlocal called
        del args, kwargs
        called = True
        return _RuntimeResult()

    report = execute_five_layer_memory_activation(
        _body(launch_video=True),
        workdir=checkout,
        maintenance_report=_report,
        command_runner=_runner,
        runtime_installer=installer,
    )

    assert report.startswith("BLOCKED:")
    assert called is False
    assert "step=ensure_canonical_memory status=failed" in report
    assert "reason=canonical_memory_existing_invalid" in report
    assert "install_video_understanding" not in report
    assert "not a sqlite database" not in report
    assert str(tmp_path) not in report


def test_activation_executor_canonical_memory_failure_output_is_public_safe(
    monkeypatch, tmp_path: Path
) -> None:
    checkout = _prepare_legacy_configured_private_root(monkeypatch, tmp_path)

    def fail_init(self, *, import_manifest: bool = True):
        del self, import_manifest
        raise RuntimeError(f"raw private failure {tmp_path}")

    monkeypatch.setattr(PrivateMemoryStack, "init", fail_init)

    report = execute_five_layer_memory_activation(
        _body(launch_video=True),
        workdir=checkout,
        maintenance_report=_report,
        command_runner=_runner,
        runtime_installer=lambda *args, **kwargs: _RuntimeResult(),
    )

    assert report.startswith("BLOCKED:")
    assert "step=ensure_canonical_memory status=failed" in report
    assert "reason=canonical_memory_initialize_failed" in report
    assert "raw private failure" not in report
    assert str(tmp_path) not in report
    assert "install_video_understanding" not in report


def test_activation_executor_rejects_video_launch_without_exact_approval(
    tmp_path: Path,
) -> None:
    report = execute_five_layer_memory_activation(
        _body(launch_video=True, video_approval="wrong"),
        workdir=tmp_path,
        maintenance_report=_report,
        command_runner=_runner,
    )
    assert report.startswith("BLOCKED:")
    assert "reason=video_runtime_approval_invalid" in report


def test_activation_executor_surfaces_safe_video_installer_reason(
    monkeypatch, tmp_path: Path
) -> None:
    checkout = _prepare_explicit_private_root(monkeypatch, tmp_path)

    def installer(*args, **kwargs):
        del args, kwargs
        raise VideoUnderstandingError("OLLAMA_MODEL_MISSING", "private details")

    report = execute_five_layer_memory_activation(
        _body(launch_video=True),
        workdir=checkout,
        maintenance_report=_report,
        command_runner=_runner,
        runtime_installer=installer,
    )
    assert report.startswith("BLOCKED:")
    assert "step=execute_activation status=done" in report
    assert "step=install_video_understanding status=failed" in report
    assert "reason=OLLAMA_MODEL_MISSING" in report
    assert "private details" not in report


def test_activation_executor_rejects_invalid_video_receipt(
    monkeypatch, tmp_path: Path
) -> None:
    checkout = _prepare_explicit_private_root(monkeypatch, tmp_path)
    report = execute_five_layer_memory_activation(
        _body(launch_video=True),
        workdir=checkout,
        maintenance_report=_report,
        command_runner=_runner,
        runtime_installer=lambda *args, **kwargs: _RuntimeResult(worker_count=2),
    )
    assert report.startswith("BLOCKED:")
    assert "reason=video_runtime_worker_count_invalid" in report


def test_activation_executor_rejects_wrong_approval(tmp_path: Path) -> None:
    report = execute_five_layer_memory_activation(
        _body(approval="wrong"),
        workdir=tmp_path,
        maintenance_report=_report,
        command_runner=_runner,
    )
    assert report.startswith("BLOCKED:")
    assert "reason=operator_approval_invalid" in report


def test_activation_executor_fails_closed_on_receipt_check(monkeypatch, tmp_path: Path) -> None:
    checkout = _prepare_explicit_private_root(monkeypatch, tmp_path)

    def runner(
        command: list[str],
        cwd: Path,
        env: Mapping[str, str] | None,
        timeout: int,
    ) -> tuple[int, str, str]:
        code, stdout, stderr = _runner(command, cwd, env, timeout)
        if command[:3] == [
            command[0],
            "-m",
            "scripts.activate_five_layer_private_memory",
        ]:
            payload = json.loads(stdout)
            payload["booleans"]["cognee_selected"] = False
            return 1, json.dumps(payload), "private details must not surface"
        return code, stdout, stderr

    report = execute_five_layer_memory_activation(
        _body(),
        workdir=checkout,
        maintenance_report=_report,
        command_runner=runner,
    )
    assert report.startswith("BLOCKED:")
    assert "reason=activation_receipt_checks_failed" in report
    assert "private details" not in report


def test_activation_executor_surfaces_safe_blocked_reason(monkeypatch, tmp_path: Path) -> None:
    checkout = _prepare_explicit_private_root(monkeypatch, tmp_path)

    def runner(
        command: list[str],
        cwd: Path,
        env: Mapping[str, str] | None,
        timeout: int,
    ) -> tuple[int, str, str]:
        code, stdout, stderr = _runner(command, cwd, env, timeout)
        if command[:3] == [
            command[0],
            "-m",
            "scripts.activate_five_layer_private_memory",
        ]:
            payload = json.loads(stdout)
            payload["status"] = "BLOCKED"
            payload["reason_codes"] = ["cognee_selected_failed"]
            payload["booleans"] = {"private_leak_detected": False}
            payload["rollback"] = {"verified": False, "status": "not_verified"}
            return 1, json.dumps(payload), "private details must not surface"
        return code, stdout, stderr

    report = execute_five_layer_memory_activation(
        _body(),
        workdir=checkout,
        maintenance_report=_report,
        command_runner=runner,
    )
    assert report.startswith("BLOCKED:")
    assert "reason=cognee_selected_failed" in report
    assert "activation_receipt_checks_failed" not in report
    assert "private details" not in report


def test_activation_script_preserves_safe_cognee_failure_reasons() -> None:
    source = Path("scripts/activate_five_layer_private_memory.py").read_text(
        encoding="utf-8"
    )
    assert "isinstance(exc, (CogneeLocalRuntimeError, SemanticProjectionError))" in source
    assert 'reason = f"{stage}_exception"' in source
    assert '"cognee_recall_empty"' in source
    assert '"cognee_recall_invalid"' in source


def test_runner_poller_registers_activation_route() -> None:
    source = Path("scripts/runner_poll_github_tasks.py").read_text(encoding="utf-8")
    assert "ACTIVATE_FIVE_LAYER_PRIVATE_MEMORY" in source
    assert "execute_five_layer_memory_activation" in source
    assert "if task_id == ACTIVATE_FIVE_LAYER_PRIVATE_MEMORY:" in source
