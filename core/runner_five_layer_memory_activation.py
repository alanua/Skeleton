from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping

from core.private_memory_root_resolver import (
    PrivateMemoryRootResolutionError,
    resolve_private_memory_root,
)
from core.private_memory_stack import PrivateMemoryStack, PrivateMemoryStackError
from core.video_understanding.models import VideoUnderstandingError
from core.video_understanding.runtime_install import install_runtime

TASK_ID = "activate_five_layer_private_memory"
OPERATOR_APPROVAL = "EXPLICIT_FINISH_WORKING_MEMORY_20260724"
VIDEO_RUNTIME_APPROVAL = "OPERATOR_FINISH_AND_START_VIDEO_UNDERSTANDING_20260728"
RECEIPT_SCHEMA = "skeleton.five_layer_memory_activation_receipt.v2"
_MAX_OUTPUT_BYTES = 512 * 1024
_SAFE_REASON_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

_REQUIRED_TRUE_CHECKS = frozenset(
    {
        "gateway_canonical",
        "projection_queue",
        "cognee_selected",
        "mempalace_fallback",
        "graphify_fresh",
        "project_isolation",
        "revision_invalidation",
        "mandatory_bootstrap",
        "handoff_cleanup",
        "private_echo_blocked",
        "forget_verified",
        "live_status_checked",
    }
)

CommandRunner = Callable[
    [list[str], Path, Mapping[str, str] | None, int], tuple[int, str, str]
]
MaintenanceReport = Callable[[str, str, list[str], str], str]
RuntimeInstaller = Callable[..., object]


def execute_five_layer_memory_activation(
    body: str,
    *,
    workdir: str | Path,
    maintenance_report: MaintenanceReport,
    command_runner: CommandRunner | None = None,
    runtime_installer: RuntimeInstaller | None = None,
) -> str:
    runner = command_runner or _run_command
    expected_sha = _body_field(body, "Expected Main SHA")
    approval = _body_field(body, "Operator Approval")
    if expected_sha is None or _SHA_RE.fullmatch(expected_sha) is None:
        return maintenance_report(
            "BLOCKED",
            TASK_ID,
            ["reason=expected_main_sha_invalid"],
            "not_met",
        )
    if approval != OPERATOR_APPROVAL:
        return maintenance_report(
            "BLOCKED",
            TASK_ID,
            ["reason=operator_approval_invalid"],
            "not_met",
        )

    video_launch, video_reason = _video_launch_request(body)
    if video_reason is not None:
        return maintenance_report(
            "BLOCKED",
            TASK_ID,
            [f"reason={video_reason}"],
            "not_met",
        )

    checkout = Path(workdir).resolve()
    preflight, reason = _preflight_checkout(checkout, expected_sha, runner)
    if reason is not None:
        return maintenance_report(
            "BLOCKED", TASK_ID, [f"reason={reason}"], "not_met"
        )
    assert preflight is not None

    try:
        private_root, _root_source = resolve_private_memory_root(
            os.environ,
            checkout=checkout,
        )
    except PrivateMemoryRootResolutionError as exc:
        return maintenance_report(
            "BLOCKED",
            TASK_ID,
            [f"reason={exc.reason_code}"],
            "not_met",
        )

    model_config, reason = _select_local_models(checkout, runner)
    if reason is not None:
        return maintenance_report(
            "BLOCKED", TASK_ID, [f"reason={reason}"], "not_met"
        )
    assert model_config is not None

    child_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(checkout),
        "PYTHONDONTWRITEBYTECODE": "1",
        "SKELETON_RUNNER_PRIVATE_MEMORY_ROOT": private_root,
        **model_config,
    }
    command = [
        sys.executable,
        "-m",
        "scripts.activate_five_layer_private_memory",
        "--expected-sha",
        expected_sha,
        "--operator-approval",
        OPERATOR_APPROVAL,
    ]
    code, stdout, _stderr = runner(command, checkout, child_env, 2700)
    receipt, reason = _parse_activation_receipt(stdout, expected_sha)
    if reason is not None:
        return maintenance_report(
            "BLOCKED",
            TASK_ID,
            [
                "step=execute_activation status=failed",
                f"reason={reason}",
            ],
            "not_met",
        )
    assert receipt is not None
    if code != 0 or receipt.get("status") != "DONE":
        reason_codes = receipt.get("reason_codes")
        public_reason = (
            str(reason_codes[0])
            if isinstance(reason_codes, list) and reason_codes
            else "activation_failed"
        )
        return maintenance_report(
            "BLOCKED",
            TASK_ID,
            [
                "step=execute_activation status=failed",
                f"reason={_safe_reason(public_reason)}",
            ],
            "not_met",
        )

    booleans = receipt["booleans"]
    assert isinstance(booleans, dict)
    check_count = len(_REQUIRED_TRUE_CHECKS)
    resource_totals = receipt.get("resource_totals")
    disk_bytes = 0
    if isinstance(resource_totals, dict):
        value = resource_totals.get("disk_bytes")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            disk_bytes = min(value, 10**12)

    status_lines = [
        "repository=alanua/Skeleton",
        f"head_sha={expected_sha}",
        "step=verify_checkout status=done",
        "step=select_local_models status=done",
        "step=execute_activation status=done",
        f"runtime_smoke_check_count={check_count}",
        "test_summary=five_layer_private_memory_activation_done",
        f"disk_bytes={disk_bytes}",
    ]
    if video_launch:
        memory_lines, memory_reason = _ensure_canonical_memory_ready(private_root)
        if memory_reason is not None:
            return maintenance_report(
                "BLOCKED",
                TASK_ID,
                [
                    *status_lines,
                    "step=ensure_canonical_memory status=failed",
                    f"reason={memory_reason}",
                ],
                "not_met",
            )
        status_lines.extend(memory_lines)
        video_lines, video_reason = _install_video_runtime(
            checkout,
            expected_sha=expected_sha,
            private_root=private_root,
            installer=runtime_installer or install_runtime,
        )
        if video_reason is not None:
            return maintenance_report(
                "BLOCKED",
                TASK_ID,
                [
                    *status_lines,
                    "step=install_video_understanding status=failed",
                    f"reason={video_reason}",
                ],
                "not_met",
            )
        status_lines.extend(video_lines)

    return maintenance_report(
        "DONE",
        TASK_ID,
        status_lines,
        "met",
    )


def _video_launch_request(body: str) -> tuple[bool, str | None]:
    value = _body_field(body, "Launch Video Understanding")
    if value is None or value.casefold() == "false":
        return False, None
    if value.casefold() != "true":
        return False, "video_runtime_launch_invalid"
    approval = _body_field(body, "Video Runtime Approval")
    if approval != VIDEO_RUNTIME_APPROVAL:
        return False, "video_runtime_approval_invalid"
    return True, None


def _ensure_canonical_memory_ready(private_root: str) -> tuple[list[str], str | None]:
    stack = PrivateMemoryStack(private_root)
    initialized = not (Path(private_root) / "canonical.sqlite").exists()
    if initialized:
        try:
            status = stack.init(import_manifest=True)
        except PrivateMemoryStackError:
            return [], "canonical_memory_initialize_failed"
        except Exception:
            return [], "canonical_memory_initialize_failed"
    else:
        status = stack.status()

    reason = _validate_canonical_memory_status(status, existing=not initialized)
    if reason is not None:
        return [], reason
    return [
        "step=ensure_canonical_memory status=done",
        f"canonical_memory_initialized={str(initialized).lower()}",
        "canonical_memory_state=READY",
    ], None


def _validate_canonical_memory_status(
    status: object, *, existing: bool
) -> str | None:
    if not isinstance(status, Mapping):
        return (
            "canonical_memory_existing_invalid"
            if existing
            else "canonical_memory_not_ready"
        )
    if status.get("state") != "READY":
        return (
            "canonical_memory_existing_invalid"
            if existing and status.get("state") == "BLOCKED"
            else "canonical_memory_not_ready"
        )
    for key in ("canonical_sqlite", "mempalace", "graphify"):
        value = status.get(key)
        if not isinstance(value, Mapping):
            return (
                "canonical_memory_existing_invalid"
                if existing
                else "canonical_memory_not_ready"
            )
        if value.get("state") != "READY":
            return (
                "canonical_memory_existing_invalid"
                if existing and value.get("state") == "BLOCKED"
                else "canonical_memory_not_ready"
            )
    return None


def _install_video_runtime(
    checkout: Path,
    *,
    expected_sha: str,
    private_root: str,
    installer: RuntimeInstaller,
) -> tuple[list[str], str | None]:
    install_env = {
        **os.environ,
        "SKELETON_PRIVATE_MEMORY_ROOT": private_root,
    }
    try:
        result = installer(
            checkout,
            expected_sha=expected_sha,
            enable=True,
            env=install_env,
        )
    except VideoUnderstandingError as exc:
        return [], _safe_reason(exc.reason_code)
    except Exception:
        return [], "video_runtime_install_failed"

    public_dict = getattr(result, "public_dict", None)
    if not callable(public_dict):
        return [], "video_runtime_receipt_invalid"
    try:
        payload = public_dict()
    except Exception:
        return [], "video_runtime_receipt_invalid"
    reason = _validate_video_runtime_receipt(payload, expected_sha)
    if reason is not None:
        return [], reason
    assert isinstance(payload, Mapping)
    reasons = payload.get("stable_reason_codes")
    safe_reasons = (
        sorted(str(value) for value in reasons)
        if isinstance(reasons, list) and reasons
        else ["none"]
    )
    return [
        "step=install_video_understanding status=done",
        f"video_provider_ready_count={payload['provider_ready_count']}",
        f"video_provider_required_count={payload['provider_required_count']}",
        f"video_ollama_status={payload['ollama_status']}",
        f"video_sona_status={payload['sona_status']}",
        f"video_memory_gateway_status={payload['memory_gateway_status']}",
        f"video_memory_roundtrip_status={payload['memory_roundtrip_status']}",
        f"video_service_install_status={payload['service_install_status']}",
        "video_service_active=true",
        "video_worker_count=1",
        "video_rollback_ready=true",
        f"video_stable_reason_codes={','.join(safe_reasons)}",
    ], None


def _validate_video_runtime_receipt(
    payload: object, expected_sha: str
) -> str | None:
    if not isinstance(payload, Mapping):
        return "video_runtime_receipt_invalid"
    if payload.get("source_merge_sha") != expected_sha:
        return "video_runtime_source_mismatch"
    ready = payload.get("provider_ready_count")
    required = payload.get("provider_required_count")
    if (
        isinstance(ready, bool)
        or isinstance(required, bool)
        or not isinstance(ready, int)
        or not isinstance(required, int)
        or ready < 0
        or required <= 0
        or ready != required
    ):
        return "video_runtime_provider_blocked"
    exact_values = {
        "runtime_config_status": "READY",
        "ollama_status": "READY",
        "artifact_store_status": "READY",
        "queue_recovery_status": "DONE",
        "memory_gateway_status": "READY",
        "memory_roundtrip_status": "DONE",
        "service_install_status": "ACTIVE",
    }
    for field, expected in exact_values.items():
        if payload.get(field) != expected:
            return f"video_runtime_{field}_blocked"
    if payload.get("service_active") is not True:
        return "video_runtime_service_inactive"
    if payload.get("worker_count") != 1:
        return "video_runtime_worker_count_invalid"
    if payload.get("rollback_ready") is not True:
        return "video_runtime_rollback_unverified"
    sona = payload.get("sona_status")
    if sona not in {"READY", "BLOCKED"}:
        return "video_runtime_sona_status_invalid"
    reasons = payload.get("stable_reason_codes")
    if not isinstance(reasons, list):
        return "video_runtime_reason_codes_invalid"
    if any(
        not isinstance(value, str) or _SAFE_REASON_RE.fullmatch(value) is None
        for value in reasons
    ):
        return "video_runtime_reason_codes_invalid"
    return None


def _preflight_checkout(
    checkout: Path,
    expected_sha: str,
    runner: CommandRunner,
) -> tuple[dict[str, str] | None, str | None]:
    commands = (
        (["git", "rev-parse", "HEAD"], "head"),
        (["git", "branch", "--show-current"], "branch"),
        (["git", "status", "--porcelain"], "status"),
        (["git", "remote", "get-url", "origin"], "origin"),
    )
    values: dict[str, str] = {}
    for command, key in commands:
        code, stdout, _stderr = runner(command, checkout, None, 60)
        if code != 0 or len(stdout.encode("utf-8")) > 8192:
            return None, f"checkout_{key}_read_failed"
        values[key] = stdout.strip()
    if values["head"] != expected_sha:
        return None, "checkout_head_mismatch"
    if values["branch"] != "main":
        return None, "checkout_branch_not_main"
    if values["status"]:
        return None, "checkout_dirty"
    normalized_origin = values["origin"].removesuffix(".git").rstrip("/")
    if not normalized_origin.endswith("github.com/alanua/Skeleton"):
        return None, "checkout_origin_mismatch"
    return values, None


def _select_local_models(
    checkout: Path, runner: CommandRunner
) -> tuple[dict[str, str] | None, str | None]:
    code, stdout, _stderr = runner(["ollama", "list"], checkout, None, 60)
    if code != 0 or len(stdout.encode("utf-8")) > 128 * 1024:
        return None, "ollama_unavailable"
    models = [
        line.split()[0]
        for line in stdout.splitlines()[1:]
        if line.strip() and line.split()
    ]
    embedding = next(
        (
            model
            for model in models
            if any(
                token in model.casefold()
                for token in ("nomic-embed-text", "mxbai-embed-large", "all-minilm")
            )
        ),
        None,
    )
    llm = next(
        (
            model
            for model in models
            if "embed" not in model.casefold() and "minilm" not in model.casefold()
        ),
        None,
    )
    if embedding is None:
        return None, "local_embedding_model_unavailable"
    if llm is None:
        return None, "local_llm_model_unavailable"
    lowered = embedding.casefold()
    if "nomic-embed-text" in lowered:
        dimensions, tokenizer = "768", "nomic-ai/nomic-embed-text-v1.5"
    elif "mxbai-embed-large" in lowered:
        dimensions, tokenizer = "1024", "mixedbread-ai/mxbai-embed-large-v1"
    elif "all-minilm" in lowered:
        dimensions, tokenizer = "384", "sentence-transformers/all-MiniLM-L6-v2"
    else:
        return None, "local_embedding_model_unsupported"
    return {
        "SKELETON_COGNEE_LLM_PROVIDER": "ollama",
        "SKELETON_COGNEE_LLM_MODEL": llm,
        "SKELETON_COGNEE_LLM_ENDPOINT": "http://127.0.0.1:11434/v1",
        "SKELETON_COGNEE_EMBEDDING_PROVIDER": "ollama",
        "SKELETON_COGNEE_EMBEDDING_MODEL": embedding,
        "SKELETON_COGNEE_EMBEDDING_DIMENSIONS": dimensions,
        "SKELETON_COGNEE_EMBEDDING_ENDPOINT": "http://127.0.0.1:11434/api/embed",
        "SKELETON_COGNEE_HUGGINGFACE_TOKENIZER": tokenizer,
    }, None


def _parse_activation_receipt(
    stdout: str, expected_sha: str
) -> tuple[dict[str, object] | None, str | None]:
    if len(stdout.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        return None, "activation_receipt_oversized"
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        return None, "activation_receipt_invalid"
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError:
        return None, "activation_receipt_invalid"
    if not isinstance(payload, dict):
        return None, "activation_receipt_invalid"
    if payload.get("schema") != RECEIPT_SCHEMA:
        return None, "activation_receipt_schema_invalid"
    if payload.get("source_sha") != expected_sha:
        return None, "activation_receipt_source_mismatch"
    booleans = payload.get("booleans")
    if not isinstance(booleans, dict):
        return None, "activation_receipt_checks_invalid"
    if booleans.get("private_leak_detected") is not False:
        return None, "activation_private_leak_detected"
    status = payload.get("status")
    if status == "BLOCKED":
        reason_codes = payload.get("reason_codes")
        if (
            not isinstance(reason_codes, list)
            or len(reason_codes) != 1
            or not isinstance(reason_codes[0], str)
            or _SAFE_REASON_RE.fullmatch(reason_codes[0]) is None
        ):
            return None, "activation_receipt_reason_invalid"
        return payload, None
    if status != "DONE":
        return None, "activation_receipt_status_invalid"
    if any(booleans.get(key) is not True for key in _REQUIRED_TRUE_CHECKS):
        return None, "activation_receipt_checks_failed"
    rollback = payload.get("rollback")
    if not isinstance(rollback, dict) or rollback.get("verified") is not True:
        return None, "activation_rollback_unverified"
    return payload, None


def _body_field(body: str, name: str) -> str | None:
    match = re.search(
        rf"(?mi)^\s*{re.escape(name)}\s*:\s*([^\r\n]+?)\s*$", body or ""
    )
    return match.group(1).strip() if match is not None else None


def _safe_reason(value: str) -> str:
    return value if _SAFE_REASON_RE.fullmatch(value) else "activation_failed"


def _run_command(
    command: list[str],
    cwd: Path,
    env: Mapping[str, str] | None,
    timeout: int,
) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, "", ""
    return completed.returncode, completed.stdout, completed.stderr
