from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import pwd
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4


EXEC_REQUEST_SCHEMA = "skeleton.home_edge.exec_request.v1"
EXEC_RECEIPT_SCHEMA = "skeleton.home_edge.exec_receipt.v1"
DEFAULT_NODE_ID = "home-edge-01"
DEFAULT_MAX_OUTPUT_BYTES = 64_000
DEFAULT_MAX_STDIN_BYTES = 256_000
DEFAULT_MAX_TIMEOUT_SECONDS = 900
DEFAULT_FRESHNESS_SECONDS = 300
DEFAULT_AUDIT_LOG = Path.home() / ".local/state/skeleton/home_edge_exec/audit.jsonl"
DEFAULT_IDEMPOTENCY_CACHE = Path.home() / ".local/state/skeleton/home_edge_exec/idempotency.json"
DEFAULT_CANCEL_DIR = Path.home() / ".local/state/skeleton/home_edge_exec/cancel"
DEFAULT_STATE_FILE = Path.home() / ".local/state/skeleton/home_edge_exec/state.json"
SIGNATURE_PREFIX = "sha256="
PUBLIC_REDACTION = "[redacted]"
PUBLIC_ERROR_MESSAGE = "home_edge_exec request blocked"
PUBLIC_SUMMARY_LIMIT = 240
ROOT_READ_ONLY_APPROVAL_PREFIX = "root-read-only:"
PRESERVED_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM")
REQUEST_ENV_ALLOWLIST = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "PULSE_SERVER",
    "PIPEWIRE_REMOTE",
    "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_TYPE",
)
SENSITIVE_PUBLIC_RE = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "private_key",
    "ssh_key",
    "authorization",
)


class ExecutionLane(StrEnum):
    READ_ONLY = "read_only"
    ROUTINE_MUTATION = "routine_mutation"
    PRIVILEGED_MUTATION = "privileged_mutation"
    DESTRUCTIVE = "destructive"


class ExecutionUser(StrEnum):
    DESKTOP_USER = "desktop-user"
    ROOT = "root"


class ExecMode(StrEnum):
    ARGV = "argv"
    SCRIPT = "script"


class HomeEdgeExecError(ValueError):
    """Raised when a Home Edge exec request is outside the universal contract."""


@dataclass(frozen=True)
class HomeEdgeExecRequest:
    request_id: str
    node_id: str
    execution_lane: ExecutionLane
    argv: tuple[str, ...] = ()
    stdin_text: str | None = None
    stdin_base64: str | None = None
    cwd: str | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: int = 30
    operator_approval_ref: str | None = None
    idempotency_key: str | None = None
    run_as: ExecutionUser = ExecutionUser.DESKTOP_USER
    mode: ExecMode = ExecMode.ARGV
    script: str | None = None
    script_interpreter: str = "bash"
    timestamp: str | None = None
    nonce: str | None = None
    signature: str | None = None
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    public: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "HomeEdgeExecRequest":
        if not isinstance(data, Mapping):
            raise HomeEdgeExecError("request must be an object")
        lane = _enum(ExecutionLane, data.get("execution_lane"), "execution_lane")
        run_as = _enum(ExecutionUser, data.get("run_as", ExecutionUser.DESKTOP_USER), "run_as")
        mode = _enum(ExecMode, data.get("mode", ExecMode.ARGV), "mode")
        request = cls(
            request_id=_optional_string(data.get("request_id")) or f"home-edge-exec-{uuid4()}",
            node_id=_required_string(data.get("node_id"), "node_id"),
            execution_lane=lane,
            argv=tuple(_string_list(data.get("argv", []), "argv")),
            stdin_text=_optional_string(data.get("stdin_text")),
            stdin_base64=_optional_string(data.get("stdin_base64")),
            cwd=_optional_string(data.get("cwd")),
            environment=dict(_string_map(data.get("environment", {}), "environment")),
            timeout_seconds=_bounded_int(data.get("timeout_seconds", 30), "timeout_seconds", 1, DEFAULT_MAX_TIMEOUT_SECONDS),
            operator_approval_ref=_optional_string(data.get("operator_approval_ref")),
            idempotency_key=_optional_string(data.get("idempotency_key")),
            run_as=run_as,
            mode=mode,
            script=_optional_string(data.get("script")),
            script_interpreter=_optional_string(data.get("script_interpreter")) or "bash",
            timestamp=_optional_string(data.get("timestamp")),
            nonce=_optional_string(data.get("nonce")),
            signature=_optional_string(data.get("signature")),
            max_output_bytes=_bounded_int(
                data.get("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES),
                "max_output_bytes",
                1_024,
                1_000_000,
            ),
            public=bool(data.get("public", False)),
        )
        request.validate()
        return request

    def validate(self) -> None:
        if self.node_id != DEFAULT_NODE_ID:
            raise HomeEdgeExecError("request node_id is not bound to home-edge-01")
        if self.stdin_text is not None and self.stdin_base64 is not None:
            raise HomeEdgeExecError("request may provide stdin_text or stdin_base64, not both")
        if self.execution_lane in {
            ExecutionLane.ROUTINE_MUTATION,
            ExecutionLane.PRIVILEGED_MUTATION,
            ExecutionLane.DESTRUCTIVE,
        } and not self.operator_approval_ref:
            raise HomeEdgeExecError(f"{self.execution_lane.value} lane requires per-request operator approval")
        if self.execution_lane is ExecutionLane.ROUTINE_MUTATION and self.run_as is not ExecutionUser.DESKTOP_USER:
            raise HomeEdgeExecError("routine_mutation lane must run as desktop-user")
        if self.execution_lane in {ExecutionLane.PRIVILEGED_MUTATION, ExecutionLane.DESTRUCTIVE} and self.run_as is not ExecutionUser.ROOT:
            raise HomeEdgeExecError("privileged and destructive lanes must run as root")
        if (
            self.execution_lane is ExecutionLane.READ_ONLY
            and self.run_as is ExecutionUser.ROOT
            and not (self.operator_approval_ref or "").startswith(ROOT_READ_ONLY_APPROVAL_PREFIX)
        ):
            raise HomeEdgeExecError("root read_only requires separate root-read-only operator approval")
        if self.mode is ExecMode.ARGV:
            if not self.argv:
                raise HomeEdgeExecError("argv mode requires a non-empty argv array")
            if self.script is not None:
                raise HomeEdgeExecError("argv mode must not include script")
        if self.mode is ExecMode.SCRIPT:
            if self.argv:
                raise HomeEdgeExecError("script mode must not include argv")
            if not self.script:
                raise HomeEdgeExecError("script mode requires script text")
            if self.script_interpreter not in {"bash", "python3"}:
                raise HomeEdgeExecError("script_interpreter must be bash or python3")
        self.stdin_bytes()

    def stdin_bytes(self) -> bytes | None:
        if self.stdin_text is not None:
            data = self.stdin_text.encode()
        elif self.stdin_base64 is not None:
            try:
                data = base64.b64decode(self.stdin_base64, validate=True)
            except ValueError as exc:
                raise HomeEdgeExecError("stdin_base64 must be valid base64") from exc
        else:
            return None
        if len(data) > DEFAULT_MAX_STDIN_BYTES:
            raise HomeEdgeExecError("stdin payload exceeds bounded input limit")
        return data

    def canonical_for_signature(self) -> dict[str, Any]:
        return self.to_mapping(include_signature=False)

    def command_argv(self, *, current_user: ExecutionUser) -> list[str]:
        command = list(self.argv) if self.mode is ExecMode.ARGV else _script_argv(self.script_interpreter, self.script or "")
        if self.run_as is current_user:
            return command
        if self.run_as is ExecutionUser.ROOT:
            return ["sudo", "--non-interactive", "--"] + command
        return ["sudo", "--non-interactive", "-u", _desktop_account().pw_name, "--"] + command

    def to_mapping(self, *, include_signature: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": EXEC_REQUEST_SCHEMA,
            "request_id": self.request_id,
            "node_id": self.node_id,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "environment": dict(sorted(self.environment.items())),
            "timeout_seconds": self.timeout_seconds,
            "execution_lane": self.execution_lane.value,
            "operator_approval_ref": self.operator_approval_ref,
            "idempotency_key": self.idempotency_key,
            "run_as": self.run_as.value,
            "mode": self.mode.value,
            "script": self.script,
            "script_interpreter": self.script_interpreter,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "max_output_bytes": self.max_output_bytes,
            "public": self.public,
        }
        if self.stdin_text is not None:
            payload["stdin_text"] = self.stdin_text
        if self.stdin_base64 is not None:
            payload["stdin_base64"] = self.stdin_base64
        if include_signature:
            payload["signature"] = self.signature
        return {key: value for key, value in payload.items() if value is not None}


@dataclass(frozen=True)
class HomeEdgeExecReceipt:
    status: str
    request_id: str
    node_id: str
    execution_lane: str
    exit_code: int | None
    stdout: str
    stderr: str
    started_at: str
    finished_at: str
    duration_seconds: float
    idempotency: str
    receipt_hash: str
    error: str | None = None
    argv: tuple[str, ...] = ()
    public: bool = False

    def to_mapping(self) -> dict[str, Any]:
        if self.public:
            payload = {
                "schema": EXEC_RECEIPT_SCHEMA,
                "public": True,
                "status": self.status,
                "request_id": self.request_id,
                "node_id": self.node_id,
                "execution_lane": self.execution_lane,
                "exit_code": self.exit_code,
                "summary": _public_summary(self.status, self.exit_code, self.error),
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "duration_seconds": self.duration_seconds,
                "idempotency": self.idempotency,
                "receipt_hash": self.receipt_hash,
            }
            return {key: value for key, value in payload.items() if value is not None}
        payload = {
            "schema": EXEC_RECEIPT_SCHEMA,
            "status": self.status,
            "request_id": self.request_id,
            "node_id": self.node_id,
            "execution_lane": self.execution_lane,
            "exit_code": self.exit_code,
            "stdout": _redact_public(self.stdout) if self.public else self.stdout,
            "stderr": _redact_public(self.stderr) if self.public else self.stderr,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "idempotency": self.idempotency,
            "receipt_hash": self.receipt_hash,
            "error": _redact_public(self.error) if self.public and self.error else self.error,
            "argv": ["<script>"] if self.public and not self.argv else list(self.argv),
        }
        return {key: value for key, value in payload.items() if value is not None}

    def to_private_mapping(self) -> dict[str, Any]:
        return replace(self, public=False).to_mapping()


class HomeEdgeExecEngine:
    def __init__(
        self,
        *,
        audit_log: str | Path | None = DEFAULT_AUDIT_LOG,
        idempotency_cache: str | Path | None = DEFAULT_IDEMPOTENCY_CACHE,
        hmac_secret: str | None = None,
        current_user: ExecutionUser | None = None,
        freshness_seconds: int = DEFAULT_FRESHNESS_SECONDS,
        cancel_dir: str | Path | None = DEFAULT_CANCEL_DIR,
        state_file: str | Path | None = None,
    ) -> None:
        self.audit_log = Path(audit_log) if audit_log is not None else None
        self.state_file = Path(state_file) if state_file is not None else (Path(idempotency_cache) if idempotency_cache is not None else None)
        self.idempotency_cache = self.state_file
        self.hmac_secret = hmac_secret
        self.current_user = current_user or _effective_execution_user()
        self.freshness_seconds = freshness_seconds
        self.cancel_dir = Path(cancel_dir) if cancel_dir is not None else None

    def execute(self, request: HomeEdgeExecRequest | Mapping[str, Any]) -> HomeEdgeExecReceipt:
        parsed = request if isinstance(request, HomeEdgeExecRequest) else HomeEdgeExecRequest.from_mapping(request)
        self._verify_request(parsed)
        if self.state_file is None:
            raise HomeEdgeExecError("persistent nonce/idempotency state is not configured")
        self.state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with _locked_json_state(self.state_file) as state:
            replay = self._cached_receipt(parsed, state)
            if replay is not None:
                return replay
            self._reserve_request(parsed, state)
            receipt = self._execute_once(parsed, idempotency="executed")
            self._store_receipt(parsed, receipt, state)
            self._audit(parsed, receipt)
            return receipt

    def _execute_once(self, parsed: HomeEdgeExecRequest, *, idempotency: str) -> HomeEdgeExecReceipt:
        self._enforce_identity(parsed)

        started = datetime.now(UTC)
        start_monotonic = time.monotonic()
        status = "ok"
        exit_code: int | None = None
        stdout = ""
        stderr = ""
        error: str | None = None
        command = parsed.command_argv(current_user=self.current_user)
        try:
            completed = _run_bounded_process(
                command,
                input=parsed.stdin_bytes(),
                cwd=parsed.cwd,
                env=_child_environment(parsed.environment, run_as=parsed.run_as),
                timeout_seconds=parsed.timeout_seconds,
                cancel_file=self._cancel_file(parsed),
            )
            exit_code = completed.returncode
            stdout = _bounded_decode(completed.stdout, parsed.max_output_bytes)
            stderr = _bounded_decode(completed.stderr, parsed.max_output_bytes)
            if completed.returncode == -15 and "[cancelled]" in stderr:
                status = "cancelled"
                error = "cancelled"
            elif completed.returncode != 0:
                status = "failed"
        except subprocess.TimeoutExpired as exc:
            status = "timeout"
            exit_code = None
            stdout = _bounded_decode(exc.stdout or b"", parsed.max_output_bytes)
            stderr = _bounded_decode(exc.stderr or b"", parsed.max_output_bytes)
            error = f"timeout after {parsed.timeout_seconds}s"
        except KeyboardInterrupt:
            status = "cancelled"
            exit_code = None
            error = "cancelled"
        except Exception as exc:  # noqa: BLE001 - receipt must preserve private failure details.
            status = "blocked"
            exit_code = None
            error = f"{type(exc).__name__}: {exc}"

        finished = datetime.now(UTC)
        receipt = self._build_receipt(
            parsed,
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            error=error,
            started_at=started,
            finished_at=finished,
            duration_seconds=round(time.monotonic() - start_monotonic, 6),
            idempotency=idempotency,
            argv=tuple(command),
        )
        return receipt

    def _cancel_file(self, request: HomeEdgeExecRequest) -> Path | None:
        if self.cancel_dir is None:
            return None
        return self.cancel_dir / f"{request.request_id}.cancel"

    def _verify_request(self, request: HomeEdgeExecRequest) -> None:
        request.validate()
        if not self.hmac_secret:
            raise HomeEdgeExecError("node HMAC secret is not configured")
        if not request.timestamp or not request.nonce or not request.signature:
            raise HomeEdgeExecError("requests require timestamp, nonce and signature")
        _verify_fresh_timestamp(request.timestamp, self.freshness_seconds)
        expected = sign_request(request, self.hmac_secret)
        if not hmac.compare_digest(expected, request.signature):
            raise HomeEdgeExecError("request signature mismatch")

    def _cached_receipt(self, request: HomeEdgeExecRequest, state: Mapping[str, Any]) -> HomeEdgeExecReceipt | None:
        digest = _payload_digest(request)
        if request.idempotency_key:
            cached = _state_section(state, "idempotency").get(request.idempotency_key)
            if isinstance(cached, dict):
                if cached.get("payload_digest") != digest:
                    raise HomeEdgeExecError("idempotency key was already used for a different payload")
                receipt = cached.get("receipt")
                if isinstance(receipt, dict):
                    return receipt_from_mapping({**receipt, "idempotency": "replayed", "public": request.public})
        nonce_record = _state_section(state, "nonces").get(request.nonce or "")
        if not isinstance(nonce_record, dict):
            return None
        if nonce_record.get("payload_digest") != digest:
            raise HomeEdgeExecError("nonce was already used for a different payload")
        receipt = nonce_record.get("receipt")
        if request.idempotency_key and isinstance(receipt, dict):
            return receipt_from_mapping({**receipt, "idempotency": "replayed", "public": request.public})
        raise HomeEdgeExecError("nonce was already used")

    def _reserve_request(self, request: HomeEdgeExecRequest, state: dict[str, Any]) -> None:
        digest = _payload_digest(request)
        nonces = _mutable_state_section(state, "nonces")
        nonces[request.nonce or ""] = {"payload_digest": digest, "idempotency_key": request.idempotency_key}
        if request.idempotency_key:
            _mutable_state_section(state, "idempotency")[request.idempotency_key] = {
                "payload_digest": digest,
                "nonce": request.nonce,
            }

    def _store_receipt(self, request: HomeEdgeExecRequest, receipt: HomeEdgeExecReceipt, state: dict[str, Any]) -> None:
        digest = _payload_digest(request)
        stored = receipt.to_private_mapping()
        nonce_record = _mutable_state_section(state, "nonces").setdefault(request.nonce or "", {})
        nonce_record.update({"payload_digest": digest, "idempotency_key": request.idempotency_key, "receipt": stored})
        if request.idempotency_key:
            _mutable_state_section(state, "idempotency")[request.idempotency_key] = {
                "payload_digest": digest,
                "nonce": request.nonce,
                "receipt": stored,
            }

    def _enforce_identity(self, request: HomeEdgeExecRequest) -> None:
        if request.run_as is ExecutionUser.DESKTOP_USER:
            _desktop_account()
            if self.current_user is ExecutionUser.ROOT:
                return
            if self.current_user is not ExecutionUser.DESKTOP_USER:
                raise HomeEdgeExecError("desktop-user request requires desktop identity or root switcher")
        elif request.run_as is ExecutionUser.ROOT and self.current_user is not ExecutionUser.ROOT:
            return

    def _audit(self, request: HomeEdgeExecRequest, receipt: HomeEdgeExecReceipt) -> None:
        if self.audit_log is None:
            return
        self.audit_log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        event = {
            "schema": "skeleton.home_edge.exec_audit.v1",
            "request_id": request.request_id,
            "node_id": request.node_id,
            "execution_lane": request.execution_lane.value,
            "run_as": request.run_as.value,
            "mode": request.mode.value,
            "idempotency_key_hash": _hash_text(request.idempotency_key) if request.idempotency_key else None,
            "status": receipt.status,
            "exit_code": receipt.exit_code,
            "started_at": receipt.started_at,
            "finished_at": receipt.finished_at,
            "receipt_hash": receipt.receipt_hash,
        }
        with self.audit_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")

    def _build_receipt(
        self,
        request: HomeEdgeExecRequest,
        *,
        status: str,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        error: str | None,
        started_at: datetime,
        finished_at: datetime,
        duration_seconds: float,
        idempotency: str,
        argv: tuple[str, ...],
    ) -> HomeEdgeExecReceipt:
        body = {
            "request_id": request.request_id,
            "node_id": request.node_id,
            "status": status,
            "exit_code": exit_code,
            "stdout_sha256": _hash_text(stdout),
            "stderr_sha256": _hash_text(stderr),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "execution_lane": request.execution_lane.value,
            "idempotency_key_hash": _hash_text(request.idempotency_key) if request.idempotency_key else None,
        }
        receipt_hash = _hash_json(body)
        return HomeEdgeExecReceipt(
            status=status,
            request_id=request.request_id,
            node_id=request.node_id,
            execution_lane=request.execution_lane.value,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            duration_seconds=duration_seconds,
            idempotency=idempotency,
            receipt_hash=receipt_hash,
            error=error,
            argv=argv,
            public=request.public,
        )


def sign_request(request: HomeEdgeExecRequest | Mapping[str, Any], secret: str) -> str:
    parsed = request if isinstance(request, HomeEdgeExecRequest) else HomeEdgeExecRequest.from_mapping(request)
    message = json.dumps(parsed.canonical_for_signature(), sort_keys=True, separators=(",", ":")).encode()
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def receipt_from_mapping(data: Mapping[str, Any]) -> HomeEdgeExecReceipt:
    is_public = bool(data.get("public", False)) or (
        isinstance(data.get("summary"), str) and "stdout" not in data and "stderr" not in data
    )
    return HomeEdgeExecReceipt(
        status=_required_string(data.get("status"), "status"),
        request_id=_required_string(data.get("request_id"), "request_id"),
        node_id=_required_string(data.get("node_id"), "node_id"),
        execution_lane=_required_string(data.get("execution_lane"), "execution_lane"),
        exit_code=data.get("exit_code") if isinstance(data.get("exit_code"), int) or data.get("exit_code") is None else None,
        stdout=data.get("stdout") if isinstance(data.get("stdout"), str) else "",
        stderr=data.get("stderr") if isinstance(data.get("stderr"), str) else "",
        started_at=_required_string(data.get("started_at"), "started_at"),
        finished_at=_required_string(data.get("finished_at"), "finished_at"),
        duration_seconds=float(data.get("duration_seconds", 0.0)),
        idempotency=_required_string(data.get("idempotency"), "idempotency"),
        receipt_hash=_required_string(data.get("receipt_hash"), "receipt_hash"),
        error=_optional_string(data.get("error")),
        argv=tuple(_string_list(data.get("argv", []), "argv")),
        public=is_public,
    )


def cancelled_receipt(request_id: str, *, reason: str = "cancelled") -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    body = {
        "request_id": request_id,
        "status": "cancelled",
        "finished_at": now,
        "reason": reason,
    }
    return {
        "schema": EXEC_RECEIPT_SCHEMA,
        "status": "cancelled",
        "request_id": request_id,
        "node_id": DEFAULT_NODE_ID,
        "execution_lane": "unknown",
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0.0,
        "idempotency": "not_executed",
        "receipt_hash": _hash_json(body),
        "error": reason,
    }


@dataclass(frozen=True)
class _CompletedProcess:
    returncode: int
    stdout: bytes
    stderr: bytes


def _run_bounded_process(
    command: list[str],
    *,
    input: bytes | None,
    cwd: str | None,
    env: Mapping[str, str],
    timeout_seconds: int,
    cancel_file: Path | None,
) -> _CompletedProcess:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if input is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=dict(env),
        start_new_session=os.name == "posix",
    )
    deadline = time.monotonic() + timeout_seconds
    pending_input = input
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            cancel_process_group(process)
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(command, timeout_seconds, output=stdout, stderr=stderr)
        if cancel_file is not None and cancel_file.exists():
            cancel_process_group(process)
            stdout, stderr = process.communicate()
            return _CompletedProcess(returncode=-15, stdout=stdout, stderr=stderr + b"\n[cancelled]")
        try:
            stdout, stderr = process.communicate(input=pending_input, timeout=min(remaining, 0.2))
            return _CompletedProcess(returncode=process.returncode, stdout=stdout, stderr=stderr)
        except subprocess.TimeoutExpired:
            pending_input = None


def _script_argv(interpreter: str, script: str) -> list[str]:
    if interpreter == "bash":
        return ["bash", "-c", script]
    if interpreter == "python3":
        return ["python3", "-c", script]
    raise HomeEdgeExecError("script_interpreter must be bash or python3")


def _child_environment(overrides: Mapping[str, str], *, run_as: ExecutionUser) -> dict[str, str]:
    env = {key: os.environ[key] for key in PRESERVED_ENV_KEYS if key in os.environ}
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"):
        configured = os.environ.get(f"SKELETON_HOME_EDGE_{key}")
        if configured:
            env[key] = configured
    if run_as is ExecutionUser.DESKTOP_USER:
        account = _desktop_account()
        env.update(
            {
                "HOME": account.pw_dir,
                "USER": account.pw_name,
                "LOGNAME": account.pw_name,
                "XDG_RUNTIME_DIR": f"/run/user/{account.pw_uid}",
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{account.pw_uid}/bus",
            }
        )
    else:
        env.update({"HOME": "/root", "USER": "root", "LOGNAME": "root"})
    for key, value in overrides.items():
        if key not in REQUEST_ENV_ALLOWLIST:
            raise HomeEdgeExecError(f"environment override is not permitted: {key}")
        env[key] = value
    return env


def _desktop_account() -> pwd.struct_passwd:
    username = os.environ.get("SKELETON_HOME_EDGE_DESKTOP_USER", "").strip()
    if not username:
        raise HomeEdgeExecError("desktop account is not configured")
    try:
        return pwd.getpwnam(username)
    except KeyError as exc:
        raise HomeEdgeExecError("configured desktop account cannot be resolved") from exc


def _effective_execution_user() -> ExecutionUser:
    if os.name == "posix" and os.geteuid() == 0:
        return ExecutionUser.ROOT
    return ExecutionUser.DESKTOP_USER


def _bounded_decode(data: bytes | str, limit: int) -> str:
    raw = data.encode() if isinstance(data, str) else data
    truncated = raw[:limit]
    text = truncated.decode("utf-8", errors="replace")
    if len(raw) > limit:
        text += f"\n[truncated {len(raw) - limit} bytes]"
    return text


def _verify_fresh_timestamp(value: str, freshness_seconds: int) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HomeEdgeExecError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise HomeEdgeExecError("timestamp must include timezone")
    age = abs((datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds())
    if age > freshness_seconds:
        raise HomeEdgeExecError("request timestamp is stale")


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)


def _hash_text(value: str | None) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()


def _hash_json(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _payload_digest(request: HomeEdgeExecRequest) -> str:
    return _hash_json(request.canonical_for_signature())


def _state_section(state: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    section = state.get(name)
    return section if isinstance(section, Mapping) else {}


def _mutable_state_section(state: dict[str, Any], name: str) -> dict[str, Any]:
    section = state.get(name)
    if not isinstance(section, dict):
        section = {}
        state[name] = section
    return section


@contextmanager
def _locked_json_state(path: Path) -> Iterator[dict[str, Any]]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        state = _read_json_object(path)
        try:
            yield state
        finally:
            _atomic_write_private_json(path, state)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _atomic_write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _public_summary(status: str, exit_code: int | None, error: str | None) -> str:
    if status == "ok":
        summary = "completed"
    elif status == "failed":
        summary = "process exited non-zero"
    elif status == "timeout":
        summary = "timed out"
    elif status == "cancelled":
        summary = "cancelled"
    else:
        summary = "blocked"
    if exit_code is not None:
        summary = f"{summary}; exit_code={exit_code}"
    if error and status in {"timeout", "cancelled"}:
        summary = f"{summary}; {_redact_public(error)}"
    return summary[:PUBLIC_SUMMARY_LIMIT]


def _redact_public(value: str | None) -> str | None:
    if value is None:
        return None
    redacted = value
    for marker in SENSITIVE_PUBLIC_RE:
        if marker in redacted.lower():
            return PUBLIC_REDACTION
    return redacted


def _enum(enum_type: type[StrEnum], value: Any, field_name: str) -> Any:
    if not isinstance(value, str):
        raise HomeEdgeExecError(f"{field_name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise HomeEdgeExecError(f"{field_name} is not supported: {value}") from exc


def _required_string(value: Any, field_name: str) -> str:
    parsed = _optional_string(value)
    if parsed is None:
        raise HomeEdgeExecError(f"{field_name} must be a non-empty string")
    return parsed


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise HomeEdgeExecError("string fields must be non-empty strings when provided")
    return value


def _string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise HomeEdgeExecError(f"{field_name} must be an array of non-empty strings")
    return list(value)


def _string_map(value: Any, field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise HomeEdgeExecError(f"{field_name} must be an object with string values")
    return dict(value)


def _bounded_int(value: Any, field_name: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise HomeEdgeExecError(f"{field_name} must be an integer between {minimum} and {maximum}")
    return value


def cancel_process_group(process: subprocess.Popen[bytes], *, grace_seconds: float = 1.0) -> None:
    if os.name != "posix":
        process.kill()
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
