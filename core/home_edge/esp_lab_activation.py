from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol
from uuid import uuid4

from core.home_edge.esp_lab import SCHEMA_VERSION as ESP_LAB_SCHEMA_VERSION
from core.home_edge.esp_lab import EspLabError, validate_job
from core.home_edge.executor import (
    DEFAULT_NODE_ID,
    HomeEdgeExecRequest,
    HomeEdgeExecReceipt,
    sign_request,
)
from core.home_edge.executor_gateway import execute_home_edge_request


TASK_ID = "home_edge_esp_lab_stage1a_controller_v1"
TARGET_NODE = DEFAULT_NODE_ID
EXECUTION_LANE = "read_only"
RUN_AS = "desktop-user"
REQUEST_TIMEOUT_SECONDS = 60
MAX_EXECUTOR_OUTPUT_BYTES = 128_000
IDEMPOTENCY_KEY_PREFIX = TASK_ID
OPERATOR_APPROVAL_REF = "home_edge_esp_lab_stage1a_signed_controller_v1"


class EspLabActivationError(ValueError):
    """Raised when the ESP Lab controller request leaves the signed contract."""


class EspLabRequestSigner(Protocol):
    def __call__(self, unsigned_request: Mapping[str, Any]) -> HomeEdgeExecRequest: ...


class EspLabExecutorDispatcher(Protocol):
    def __call__(self, request: Mapping[str, Any]) -> HomeEdgeExecReceipt: ...


@dataclass(frozen=True)
class HmacEspLabRequestSigner:
    secret: str

    def __call__(self, unsigned_request: Mapping[str, Any]) -> HomeEdgeExecRequest:
        request = HomeEdgeExecRequest.from_mapping(unsigned_request)
        return HomeEdgeExecRequest.from_mapping(
            {**request.to_mapping(include_signature=False), "signature": sign_request(request, self.secret)}
        )


def build_esp_lab_controller_request(
    job: Mapping[str, Any],
    *,
    request_id: str | None = None,
    idempotency_key: str | None = None,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> HomeEdgeExecRequest:
    safe_job = _controller_job(job)
    return HomeEdgeExecRequest.from_mapping(
        {
            "request_id": request_id or f"{TASK_ID}-{uuid4()}",
            "node_id": TARGET_NODE,
            "argv": [],
            "environment": {},
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "execution_lane": EXECUTION_LANE,
            "operator_approval_ref": OPERATOR_APPROVAL_REF,
            "idempotency_key": idempotency_key or f"{IDEMPOTENCY_KEY_PREFIX}-{safe_job['idempotency_key']}",
            "run_as": RUN_AS,
            "mode": "script",
            "script": CONTROLLER_SCRIPT,
            "script_interpreter": "python3",
            "stdin_text": json.dumps(safe_job, sort_keys=True, separators=(",", ":")),
            "timestamp": timestamp or datetime.now(UTC).isoformat(),
            "nonce": nonce or f"{TASK_ID}-{uuid4()}",
            "max_output_bytes": MAX_EXECUTOR_OUTPUT_BYTES,
            "public": False,
        }
    )


def sign_esp_lab_controller_request(
    unsigned_request: HomeEdgeExecRequest,
    *,
    signer: EspLabRequestSigner,
) -> HomeEdgeExecRequest:
    unsigned = unsigned_request.to_mapping(include_signature=False)
    _validate_unsigned_controller_authority(unsigned)
    signed = signer(unsigned)
    _validate_signed_controller_authority(signed, expected_unsigned=unsigned)
    return signed


def dispatch_esp_lab_controller_request(
    job: Mapping[str, Any],
    *,
    signer: EspLabRequestSigner,
    dispatcher: EspLabExecutorDispatcher = execute_home_edge_request,
) -> HomeEdgeExecReceipt:
    unsigned = build_esp_lab_controller_request(job)
    signed = sign_esp_lab_controller_request(unsigned, signer=signer)
    return dispatcher(signed.to_mapping(include_signature=True))


def _controller_job(job: Mapping[str, Any]) -> dict[str, Any]:
    try:
        safe = validate_job(dict(job))
    except EspLabError as exc:
        raise EspLabActivationError("esp_lab_controller_job_invalid") from exc
    if safe.get("execution_mode") != "read_only":
        raise EspLabActivationError("esp_lab_controller_requires_read_only_job")
    return safe


def _validate_unsigned_controller_authority(request: Mapping[str, Any]) -> None:
    if request.get("operator_approval_ref") != OPERATOR_APPROVAL_REF:
        raise EspLabActivationError("esp_lab_controller_operator_approval_mismatch")
    _validate_controller_authority(request, include_signature=False)


def _validate_signed_controller_authority(
    request: HomeEdgeExecRequest,
    *,
    expected_unsigned: Mapping[str, Any],
) -> None:
    signed = request.to_mapping(include_signature=True)
    unsigned = request.to_mapping(include_signature=False)
    if unsigned != dict(expected_unsigned):
        raise EspLabActivationError("esp_lab_controller_signed_authority_mismatch")
    if not request.signature:
        raise EspLabActivationError("esp_lab_controller_missing_signature")
    _validate_controller_authority(signed, include_signature=True)


def _validate_controller_authority(request: Mapping[str, Any], *, include_signature: bool) -> None:
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
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if request["schema"] != "skeleton.home_edge.exec_request.v1":
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if not isinstance(request["request_id"], str) or not request["request_id"].startswith(TASK_ID + "-"):
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if request["node_id"] != TARGET_NODE:
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if request["execution_lane"] != EXECUTION_LANE:
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if request["operator_approval_ref"] != OPERATOR_APPROVAL_REF:
        raise EspLabActivationError("esp_lab_controller_operator_approval_mismatch")
    if request["run_as"] != RUN_AS:
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if request["mode"] != "script" or request["script"] != CONTROLLER_SCRIPT:
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if request["script_interpreter"] != "python3":
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if request["timeout_seconds"] != REQUEST_TIMEOUT_SECONDS:
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if request["max_output_bytes"] != MAX_EXECUTOR_OUTPUT_BYTES:
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if request["argv"] != [] or request["environment"] != {}:
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if request["public"] is not False:
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if not isinstance(request["idempotency_key"], str) or not request["idempotency_key"].startswith(
        IDEMPOTENCY_KEY_PREFIX + "-"
    ):
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if not isinstance(request["timestamp"], str) or not request["timestamp"]:
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if not isinstance(request["nonce"], str) or not request["nonce"].startswith(TASK_ID + "-"):
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    if not isinstance(request["stdin_text"], str) or not request["stdin_text"]:
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")
    _controller_job(_decode_stdin_job(request["stdin_text"]))
    if include_signature and (
        not isinstance(request["signature"], str)
        or not request["signature"].startswith("sha256=")
        or len(request["signature"]) != len("sha256=") + 64
    ):
        raise EspLabActivationError("esp_lab_controller_authority_mismatch")


def _decode_stdin_job(stdin_text: str) -> Mapping[str, Any]:
    try:
        decoded = json.loads(stdin_text)
    except json.JSONDecodeError as exc:
        raise EspLabActivationError("esp_lab_controller_job_invalid") from exc
    if not isinstance(decoded, Mapping):
        raise EspLabActivationError("esp_lab_controller_job_invalid")
    if decoded.get("schema") != f"{ESP_LAB_SCHEMA_VERSION}.job":
        raise EspLabActivationError("esp_lab_controller_job_invalid")
    return decoded


CONTROLLER_SCRIPT = '''from __future__ import annotations

import json
import sys

from core.home_edge.esp_lab import inspect_job


def main() -> None:
    job = json.loads(sys.stdin.read())
    observation, receipt = inspect_job(job, execute_read_only=True)
    print(json.dumps({"observation": observation, "receipt": receipt}, sort_keys=True, separators=(",", ":")))


main()
'''
