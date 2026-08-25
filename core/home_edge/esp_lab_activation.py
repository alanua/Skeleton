from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from core.home_edge.executor import HomeEdgeExecError, HomeEdgeExecRequest
from core.home_edge.executor_gateway import execute_home_edge_request


TASK_ID = "home_edge_01_esp_lab_stage1_activation_v1"
TARGET_NODE = "home-edge-01"
EXECUTION_LANE = "privileged_mutation"
RUN_AS = "root"
REQUEST_TIMEOUT_SECONDS = 300
MAX_EXECUTOR_OUTPUT_BYTES = 8192
OPERATOR_APPROVAL_REF = "EXACT_HEAD_HOME_EDGE_ESP_LAB_STAGE1_ACTIVATION_APPROVED"
APPROVED_SOURCE_SHA = "725dfc3aedbce194c7afcc229eb44b1eec4f463a"
INSTALLER_TRUSTED_ANCESTOR_SHA = APPROVED_SOURCE_SHA
INSTALLER_REPO_PATH = "scripts/install_home_edge_esp_lab.sh"
INSTALLER_GIT_BLOB_SHA = "e2c2378660df0cbaaf02e4556a1d1887a258b863"
INSTALLER_SHA256 = "8eeef1374af6dee0451890e7db4e37e7fe8f249ec7aff55687e0a196168dbcfe"
INIT_REPO_PATH = "core/__init__.py"
INIT_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ESP_MODULE_REPO_PATH = "core/home_edge/esp_lab.py"
ESP_MODULE_GIT_BLOB_SHA = "82a9a007b880eb591f13618216fb9fd3a97d926e"
ESP_MODULE_SHA256 = "4a499602f4602b425ae4227cb297e685f072c8a4cef56d23d1dd2e3c91333fcb"
PAYLOAD_SCHEMA = "skeleton.home_edge.esp_lab_stage1_payload.v1"
RESULT_SCHEMA = "skeleton.home_edge.esp_lab_stage1_activation_result.v1"
REQUEST_ID_PREFIX = f"{TASK_ID}-{APPROVED_SOURCE_SHA}-attempt-"
NONCE_PREFIX = f"{TASK_ID}:{APPROVED_SOURCE_SHA}:attempt:"
IDEMPOTENCY_KEY_PREFIX = f"home-edge-01-esp-lab-stage1-activation-{APPROVED_SOURCE_SHA}-attempt-"
INSTALLED_SIGNER_EXECUTABLE = Path("/usr/local/libexec/skeleton/home-edge/esp-lab-stage1/signer")
INSTALLED_SIGNER_PAYLOAD = Path("/usr/local/lib/skeleton/home-edge/esp-lab-stage1/signer_payload.py")
INSTALLED_INSTALLER_SOURCE = Path("/usr/local/lib/skeleton/home-edge/esp-lab-stage1/install_home_edge_esp_lab.sh")
SIGNER_SUDO_ARGV = ("/usr/bin/sudo", "-n", str(INSTALLED_SIGNER_EXECUTABLE))
SIGNER_TIMEOUT_SECONDS = 10
SIGNER_STDIN_MAX_BYTES = 256 * 1024
PUBLIC_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:=-]+$")
SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ATTEMPT_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
STAGE1_FAILED_STDERR_SIGNATURES = {
    "BLOCKED: installer accepts stdin only\n": "stage1_installer_stdin_invalid",
    "BLOCKED: test root requires pytest guard\n": "stage1_test_root_guard_missing",
    "BLOCKED: test root must be under tmp\n": "stage1_test_root_invalid",
    "BLOCKED: test root is unsafe\n": "stage1_test_root_unsafe",
    "BLOCKED: test root mode is unsafe\n": "stage1_test_root_unsafe",
    "BLOCKED: test environment requires guard\n": "stage1_test_environment_guard_missing",
    "BLOCKED: payload size is invalid\n": "stage1_payload_invalid",
    "BLOCKED: payload is not valid json\n": "stage1_payload_invalid",
    "BLOCKED: payload keys are invalid\n": "stage1_payload_invalid",
    "BLOCKED: payload schema is invalid\n": "stage1_payload_invalid",
    "BLOCKED: source sha is invalid\n": "stage1_payload_invalid",
    "BLOCKED: payload file list is invalid\n": "stage1_payload_invalid",
    "BLOCKED: payload file keys are invalid\n": "stage1_payload_invalid",
    "BLOCKED: payload file order is invalid\n": "stage1_payload_invalid",
    "BLOCKED: payload file hash is invalid\n": "stage1_payload_invalid",
    "BLOCKED: payload file body is invalid\n": "stage1_payload_invalid",
    "BLOCKED: payload base64 is invalid\n": "stage1_payload_invalid",
    "BLOCKED: payload decoded size is invalid\n": "stage1_payload_invalid",
    "BLOCKED: payload hash mismatch\n": "stage1_payload_invalid",
    "BLOCKED: isolated package marker is invalid\n": "stage1_payload_invalid",
    "BLOCKED: os release is unavailable\n": "stage1_host_os_unavailable",
    "BLOCKED: host os is unsupported\n": "stage1_host_os_unsupported",
    "BLOCKED: installer must run as root\n": "stage1_installer_privilege_invalid",
    "BLOCKED: host is unsupported\n": "stage1_host_unsupported",
    "BLOCKED: python runtime is unavailable\n": "stage1_python_runtime_unavailable",
    "BLOCKED: runtime base is unsafe\n": "stage1_runtime_base_unsafe",
    "BLOCKED: runtime base ownership is unsafe\n": "stage1_runtime_base_unsafe",
    "BLOCKED: runtime base mode is unsafe\n": "stage1_runtime_base_unsafe",
    "BLOCKED: existing runtime target is unsafe\n": "stage1_runtime_target_unsafe",
    "BLOCKED: existing wrapper is unsafe\n": "stage1_wrapper_unsafe",
    "BLOCKED: existing wrapper ownership is unsafe\n": "stage1_wrapper_unsafe",
    "BLOCKED: test apt is unavailable\n": "stage1_test_dependency_unavailable",
    "BLOCKED: runtime target validation failed\n": "stage1_runtime_target_unsafe",
    "BLOCKED: wrapper canary failed\n": "stage1_wrapper_canary_failed",
    "BLOCKED: canary did not return a list\n": "stage1_wrapper_canary_failed",
}


def activate_esp_lab_stage1(*, repo_root: Path | None = None) -> dict[str, object]:
    root = Path.cwd() if repo_root is None else Path(repo_root)
    try:
        installer = _reviewed_current_tree_git_blob(
            root,
            INSTALLER_REPO_PATH,
            trusted_ancestor_sha=INSTALLER_TRUSTED_ANCESTOR_SHA,
            expected_blob_sha=INSTALLER_GIT_BLOB_SHA,
        )
        esp_module = _reviewed_git_blob(
            root,
            ESP_MODULE_REPO_PATH,
            expected_source_sha=APPROVED_SOURCE_SHA,
            expected_blob_sha=ESP_MODULE_GIT_BLOB_SHA,
        )
    except ValueError as exc:
        return _blocked_result(_public_reason(exc, "reviewed_source_unavailable"))
    if hashlib.sha256(installer).hexdigest() != INSTALLER_SHA256:
        return _blocked_result("installer_sha256_mismatch")
    if hashlib.sha256(esp_module).hexdigest() != ESP_MODULE_SHA256:
        return _blocked_result("esp_module_sha256_mismatch")

    try:
        unsigned = build_activation_request(installer_script=installer, esp_module=esp_module)
        signed = sign_activation_request(unsigned)
        _validate_signed_activation_request_for_transport(
            signed,
            expected_unsigned=unsigned.to_mapping(include_signature=False),
        )
    except ValueError as exc:
        return _blocked_result(_public_reason(exc, "activation_signer_rejected"))

    try:
        receipt = execute_home_edge_request(signed.to_mapping())
    except (subprocess.TimeoutExpired, TimeoutError):
        return _blocked_result("executor_transport_timeout")
    except HomeEdgeExecError:
        return _blocked_result("executor_transport_failed")
    except Exception:
        return _blocked_result("executor_transport_exception")
    return public_result_from_executor_receipt(receipt.to_mapping())


def build_activation_request(*, installer_script: bytes, esp_module: bytes) -> HomeEdgeExecRequest:
    if _git_blob_sha1(installer_script) != INSTALLER_GIT_BLOB_SHA:
        raise ValueError("installer_blob_mismatch")
    if _git_blob_sha1(esp_module) != ESP_MODULE_GIT_BLOB_SHA:
        raise ValueError("esp_module_blob_mismatch")
    try:
        script_text = installer_script.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("installer_not_utf8") from None
    payload = build_stage1_payload(esp_module=esp_module)
    stdin_text = json.dumps(payload, sort_keys=False, separators=(",", ":"))
    if len(stdin_text.encode("utf-8")) > 230_000:
        raise ValueError("activation_payload_oversize")
    attempt_token = uuid4().hex
    return HomeEdgeExecRequest.from_mapping(
        {
            "request_id": _request_id(attempt_token),
            "node_id": TARGET_NODE,
            "execution_lane": EXECUTION_LANE,
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "operator_approval_ref": OPERATOR_APPROVAL_REF,
            "idempotency_key": _idempotency_key(attempt_token),
            "run_as": RUN_AS,
            "mode": "script",
            "script": script_text,
            "script_interpreter": "bash",
            "stdin_text": stdin_text,
            "timestamp": datetime.now(UTC).isoformat(),
            "nonce": _nonce(attempt_token),
            "max_output_bytes": MAX_EXECUTOR_OUTPUT_BYTES,
            "public": False,
        }
    )


def _request_id(attempt_token: str) -> str:
    return f"{REQUEST_ID_PREFIX}{attempt_token}"


def _nonce(attempt_token: str) -> str:
    return f"{NONCE_PREFIX}{attempt_token}"


def _idempotency_key(attempt_token: str) -> str:
    return f"{IDEMPOTENCY_KEY_PREFIX}{attempt_token}"


def build_stage1_payload(*, esp_module: bytes) -> dict[str, Any]:
    if hashlib.sha256(esp_module).hexdigest() != ESP_MODULE_SHA256:
        raise ValueError("esp_module_sha256_mismatch")
    return {
        "schema": PAYLOAD_SCHEMA,
        "source_sha": APPROVED_SOURCE_SHA,
        "files": [
            {
                "path": INIT_REPO_PATH,
                "sha256": INIT_SHA256,
                "base64": "",
            },
            {
                "path": ESP_MODULE_REPO_PATH,
                "sha256": ESP_MODULE_SHA256,
                "base64": base64.b64encode(esp_module).decode("ascii"),
            },
        ],
    }


def sign_activation_request(unsigned_request: HomeEdgeExecRequest) -> HomeEdgeExecRequest:
    unsigned = unsigned_request.to_mapping(include_signature=False)
    _validate_activation_authority(unsigned, include_signature=False)
    return _sign_activation_request_with_installed_signer(unsigned)


def _sign_activation_request_with_installed_signer(unsigned: Mapping[str, Any]) -> HomeEdgeExecRequest:
    try:
        completed = subprocess.run(
            list(SIGNER_SUDO_ARGV),
            input=json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=SIGNER_TIMEOUT_SECONDS,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("activation_signer_unavailable") from None
    if completed.returncode != 0:
        raise ValueError("activation_signer_rejected")
    if len(completed.stdout) > SIGNER_STDIN_MAX_BYTES:
        raise ValueError("activation_signer_rejected")
    try:
        signed = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("activation_signer_invalid_output") from None
    if not isinstance(signed, Mapping):
        raise ValueError("activation_signer_invalid_output")
    request = HomeEdgeExecRequest.from_mapping(signed)
    _validate_signed_activation_request_for_transport(request, expected_unsigned=unsigned)
    return request


def _validate_signed_activation_request_for_transport(
    request: HomeEdgeExecRequest,
    *,
    expected_unsigned: Mapping[str, Any],
) -> None:
    signed = request.to_mapping(include_signature=True)
    unsigned = request.to_mapping(include_signature=False)
    if unsigned != dict(expected_unsigned):
        raise ValueError("activation_signer_signed_authority_mismatch")
    if not request.signature:
        raise ValueError("activation_signer_missing_signature")
    _validate_activation_authority(signed, include_signature=True)


def _validate_activation_authority(request: Mapping[str, Any], *, include_signature: bool) -> None:
    required = {
        "schema",
        "request_id",
        "node_id",
        "argv",
        "environment",
        "timeout_seconds",
        "execution_lane",
        "operator_approval_ref",
        "idempotency_key",
        "run_as",
        "mode",
        "script",
        "script_interpreter",
        "stdin_text",
        "timestamp",
        "nonce",
        "max_output_bytes",
        "public",
    }
    if include_signature:
        required.add("signature")
    if set(request) != required:
        raise ValueError("activation_signer_authority_mismatch")
    if request["schema"] != "skeleton.home_edge.exec_request.v1":
        raise ValueError("activation_signer_authority_mismatch")
    attempt_token = _attempt_token_from_authority(request)
    if request["node_id"] != TARGET_NODE or request["execution_lane"] != EXECUTION_LANE:
        raise ValueError("activation_signer_authority_mismatch")
    if request["operator_approval_ref"] != OPERATOR_APPROVAL_REF:
        raise ValueError("activation_signer_operator_approval_mismatch")
    if request["idempotency_key"] != _idempotency_key(attempt_token):
        raise ValueError("activation_signer_authority_mismatch")
    if request["run_as"] != RUN_AS or request["mode"] != "script":
        raise ValueError("activation_signer_authority_mismatch")
    if request["script_interpreter"] != "bash":
        raise ValueError("activation_signer_authority_mismatch")
    if request["timeout_seconds"] != REQUEST_TIMEOUT_SECONDS or request["max_output_bytes"] != MAX_EXECUTOR_OUTPUT_BYTES:
        raise ValueError("activation_signer_authority_mismatch")
    if request["argv"] != [] or request["environment"] != {} or request["public"] is not False:
        raise ValueError("activation_signer_authority_mismatch")
    if not isinstance(request["script"], str) or _git_blob_sha1(request["script"].encode("utf-8")) != INSTALLER_GIT_BLOB_SHA:
        raise ValueError("activation_signer_authority_mismatch")
    _validate_stage1_payload_text(request["stdin_text"])
    _validate_timestamp(request["timestamp"])
    if include_signature and (
        not isinstance(request["signature"], str)
        or not request["signature"].startswith("sha256=")
        or len(request["signature"]) != len("sha256=") + 64
    ):
        raise ValueError("activation_signer_authority_mismatch")


def _attempt_token_from_authority(request: Mapping[str, Any]) -> str:
    request_id = request.get("request_id")
    nonce = request.get("nonce")
    if not isinstance(request_id, str) or not request_id.startswith(REQUEST_ID_PREFIX):
        raise ValueError("activation_signer_authority_mismatch")
    attempt_token = request_id.removeprefix(REQUEST_ID_PREFIX)
    if ATTEMPT_TOKEN_RE.fullmatch(attempt_token) is None:
        raise ValueError("activation_signer_authority_mismatch")
    if nonce != _nonce(attempt_token):
        raise ValueError("activation_signer_authority_mismatch")
    return attempt_token


def _validate_timestamp(value: Any) -> None:
    if not isinstance(value, str):
        raise ValueError("activation_signer_timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("activation_signer_timestamp_invalid") from None
    if parsed.tzinfo is None:
        raise ValueError("activation_signer_timestamp_invalid")
    if abs((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()) > 300:
        raise ValueError("activation_signer_timestamp_stale")


def _validate_stage1_payload_text(stdin_text: Any) -> None:
    if not isinstance(stdin_text, str) or len(stdin_text.encode("utf-8")) > 230_000:
        raise ValueError("activation_payload_invalid")
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError:
        raise ValueError("activation_payload_invalid") from None
    if not isinstance(payload, dict) or list(payload.keys()) != ["schema", "source_sha", "files"]:
        raise ValueError("activation_payload_invalid")
    if payload["schema"] != PAYLOAD_SCHEMA or payload["source_sha"] != APPROVED_SOURCE_SHA:
        raise ValueError("activation_payload_invalid")
    files = payload["files"]
    if not isinstance(files, list) or len(files) != 2:
        raise ValueError("activation_payload_invalid")
    _validate_payload_file(files[0], path=INIT_REPO_PATH, sha256=INIT_SHA256, body=b"")
    esp_body = _decode_payload_body(files[1], path=ESP_MODULE_REPO_PATH, sha256=ESP_MODULE_SHA256)
    if _git_blob_sha1(esp_body) != ESP_MODULE_GIT_BLOB_SHA:
        raise ValueError("activation_payload_invalid")


def _validate_payload_file(item: Any, *, path: str, sha256: str, body: bytes) -> None:
    decoded = _decode_payload_body(item, path=path, sha256=sha256)
    if decoded != body:
        raise ValueError("activation_payload_invalid")


def _decode_payload_body(item: Any, *, path: str, sha256: str) -> bytes:
    if not isinstance(item, dict) or list(item.keys()) != ["path", "sha256", "base64"]:
        raise ValueError("activation_payload_invalid")
    if item["path"] != path or item["sha256"] != sha256 or not isinstance(item["base64"], str):
        raise ValueError("activation_payload_invalid")
    try:
        body = base64.b64decode(item["base64"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        raise ValueError("activation_payload_invalid") from None
    if hashlib.sha256(body).hexdigest() != sha256:
        raise ValueError("activation_payload_invalid")
    return body


def public_result_from_executor_receipt(receipt: Mapping[str, Any]) -> dict[str, object]:
    if receipt.get("status") == "failed" and _nonzero_int(receipt.get("exit_code")):
        return _blocked_stage1_result_from_executor_stderr(receipt)
    if receipt.get("status") != "ok" or receipt.get("exit_code") != 0:
        return _blocked_result("executor_receipt_not_ok")
    stdout = receipt.get("stdout")
    if not isinstance(stdout, str) or len(stdout.encode("utf-8")) > MAX_EXECUTOR_OUTPUT_BYTES:
        return _blocked_result("executor_stdout_invalid")
    try:
        decoded = json.loads(stdout)
    except json.JSONDecodeError:
        return _blocked_result("executor_stdout_not_json")
    if not isinstance(decoded, Mapping):
        return _blocked_result("executor_stdout_not_json")
    try:
        return sanitize_activation_result(decoded)
    except ValueError:
        return _blocked_result("executor_public_result_unsafe")


def _blocked_stage1_result_from_executor_stderr(receipt: Mapping[str, Any]) -> dict[str, object]:
    stderr = receipt.get("stderr")
    if not isinstance(stderr, str) or len(stderr.encode("utf-8")) > MAX_EXECUTOR_OUTPUT_BYTES:
        return _blocked_result("executor_receipt_not_ok")
    reason = STAGE1_FAILED_STDERR_SIGNATURES.get(stderr)
    if reason is None:
        return _blocked_result("executor_receipt_not_ok")
    return _blocked_result(reason)


def sanitize_activation_result(result: Mapping[str, Any]) -> dict[str, object]:
    required = [
        "schema",
        "runtime_state",
        "source_sha",
        "candidate_count",
        "device_canary",
        "dependency_installed_by_operation",
        "idempotent_reuse",
    ]
    if list(result.keys()) != required:
        raise ValueError("activation_result_keys_invalid")
    if result["schema"] != RESULT_SCHEMA:
        raise ValueError("activation_result_schema_invalid")
    if result["runtime_state"] != "READY":
        raise ValueError("activation_result_state_invalid")
    if result["source_sha"] != APPROVED_SOURCE_SHA:
        raise ValueError("activation_result_source_invalid")
    if not isinstance(result["candidate_count"], int) or isinstance(result["candidate_count"], bool) or result["candidate_count"] < 0:
        raise ValueError("activation_result_candidate_count_invalid")
    if result["device_canary"] not in {"awaiting_physical_device", "serial_candidates_present"}:
        raise ValueError("activation_result_canary_invalid")
    if not isinstance(result["dependency_installed_by_operation"], bool) or not isinstance(result["idempotent_reuse"], bool):
        raise ValueError("activation_result_bool_invalid")
    public = dict(result)
    public["status"] = "DONE"
    public["reason"] = "completed"
    for key, value in public.items():
        if isinstance(value, str) and PUBLIC_VALUE_RE.fullmatch(value) is None:
            raise ValueError(f"activation_result_{key}_unsafe")
    return public


def _reviewed_git_blob(
    repo_root: Path,
    repo_path: str,
    *,
    expected_source_sha: str,
    expected_blob_sha: str,
) -> bytes:
    _validate_trusted_checkout(repo_root, expected_source_sha=expected_source_sha)
    blob = _git(repo_root, "ls-tree", expected_source_sha, "--", repo_path).decode().split()
    if len(blob) < 3 or blob[2] != expected_blob_sha:
        raise ValueError("reviewed_source_blob_mismatch")
    data = _git(repo_root, "cat-file", "-p", expected_blob_sha)
    if _git_blob_sha1(data) != expected_blob_sha:
        raise ValueError("reviewed_source_blob_mismatch")
    return data


def _reviewed_current_tree_git_blob(
    repo_root: Path,
    repo_path: str,
    *,
    trusted_ancestor_sha: str,
    expected_blob_sha: str,
) -> bytes:
    _validate_trusted_checkout(repo_root, expected_source_sha=trusted_ancestor_sha)
    blob = _git(repo_root, "ls-tree", "HEAD", "--", repo_path).decode().split()
    if len(blob) < 3 or blob[2] != expected_blob_sha:
        raise ValueError("reviewed_source_blob_mismatch")
    data = _git(repo_root, "cat-file", "-p", expected_blob_sha)
    if _git_blob_sha1(data) != expected_blob_sha:
        raise ValueError("reviewed_source_blob_mismatch")
    return data


def _validate_trusted_checkout(repo_root: Path, *, expected_source_sha: str) -> None:
    head = _git(repo_root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    if SOURCE_SHA_RE.fullmatch(head) is None:
        raise ValueError("current_head_unavailable")
    dirty = _git(repo_root, "status", "--porcelain").decode().strip()
    if dirty:
        raise ValueError("reviewed_checkout_dirty")
    try:
        subprocess.check_call(
            ["git", "merge-base", "--is-ancestor", expected_source_sha, "HEAD"],
            cwd=repo_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        raise ValueError("approved_source_not_ancestor") from None


def _git(repo_root: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(["git", *args], cwd=repo_root, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        raise ValueError("approved_source_unavailable") from None


def _git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def _nonzero_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value != 0


def _blocked_result(reason: str) -> dict[str, object]:
    return {
        "schema": RESULT_SCHEMA,
        "status": "BLOCKED",
        "reason": reason if PUBLIC_VALUE_RE.fullmatch(reason) else "blocked",
        "runtime_state": "BLOCKED",
        "source_sha": APPROVED_SOURCE_SHA,
        "candidate_count": 0,
        "device_canary": "unknown",
        "dependency_installed_by_operation": False,
        "idempotent_reuse": False,
    }


def _public_reason(exc: ValueError, fallback: str) -> str:
    reason = exc.args[0] if exc.args else fallback
    return reason if isinstance(reason, str) and PUBLIC_VALUE_RE.fullmatch(reason) else fallback
