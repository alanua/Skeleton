from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import http.server
import json
import ssl
import time
import urllib.parse
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.home_edge.esp_lab import EspLabError, inspect_job, validate_job, write_json_private, validate_output_target


CONNECTOR_VERSION = "skeleton.home_edge.esp_lab.connector.v1"
MAX_BODY_BYTES = 32768
MAX_CLOCK_SKEW_SECONDS = 300
MAX_CACHE_ITEMS = 1024
JOB_PATH = "/v1/esp-lab/jobs"
HEALTH_PATH = "/v1/esp-lab/health"


class ConnectorError(ValueError):
    def __init__(self, code: str, message: str | None = None, status: int = 400) -> None:
        super().__init__(message or code)
        self.code = code
        self.status = status


@dataclass
class ConnectorConfig:
    node_id: str
    shared_secret: bytes
    bind_host: str = "127.0.0.1"
    port: int = 0
    tls_cert: str | None = None
    tls_key: str | None = None
    allow_lan: bool = False
    allow_read_only_execution: bool = False
    allowed_node_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.allowed_node_ids:
            self.allowed_node_ids.add(self.node_id)
        validate_connector_config(self)


@dataclass
class SignedResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class ReplayCache:
    def __init__(self, *, ttl_seconds: int = MAX_CLOCK_SKEW_SECONDS, max_items: int = MAX_CACHE_ITEMS) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self.nonces: OrderedDict[str, float] = OrderedDict()
        self.idempotency: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def check(self, *, nonce: str, idempotency_key: str, body_hash: str, now: float) -> None:
        self.expire(now)
        if nonce in self.nonces:
            raise ConnectorError("nonce_replay", status=409)
        existing = self.idempotency.get(idempotency_key)
        if existing and existing[0] != body_hash:
            raise ConnectorError("idempotency_mismatch", status=409)
        self.nonces[nonce] = now
        self.idempotency[idempotency_key] = (body_hash, now)
        self._trim(self.nonces)
        self._trim(self.idempotency)

    def expire(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        for cache in (self.nonces, self.idempotency):
            while cache:
                _key, value = next(iter(cache.items()))
                ts = value if isinstance(value, (int, float)) else value[1]
                if ts >= cutoff:
                    break
                cache.popitem(last=False)

    def _trim(self, cache: OrderedDict[str, Any]) -> None:
        while len(cache) > self.max_items:
            cache.popitem(last=False)


def load_secret_file(path: str | Path) -> bytes:
    secret = Path(path).read_bytes().strip()
    if len(secret) < 16:
        raise ConnectorError("invalid_secret")
    return secret


def validate_connector_config(config: ConnectorConfig) -> None:
    loopback = config.bind_host in {"127.0.0.1", "::1", "localhost"}
    if not loopback:
        if not config.allow_lan:
            raise ConnectorError("lan_bind_requires_flag")
        if not config.tls_cert or not config.tls_key or not config.shared_secret or not config.allowed_node_ids:
            raise ConnectorError("lan_bind_requires_tls_auth_node")
    if config.allow_lan and (not config.tls_cert or not config.tls_key):
        raise ConnectorError("lan_bind_requires_tls")


def canonical_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_signature_text(
    *,
    version: str,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    idempotency_key: str,
    body_sha256: str,
) -> bytes:
    return "\n".join([version, method.upper(), path, timestamp, nonce, idempotency_key, body_sha256]).encode("utf-8")


def sign(secret: bytes, **parts: str) -> str:
    return hmac.new(secret, canonical_signature_text(**parts), hashlib.sha256).hexdigest()


def verify_signature(secret: bytes, signature: str, **parts: str) -> None:
    expected = sign(secret, **parts)
    if not hmac.compare_digest(signature, expected):
        raise ConnectorError("invalid_signature", status=401)


def require_json_content(headers: dict[str, str]) -> None:
    content_type = headers.get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise ConnectorError("unsupported_content_type", status=415)


def parse_signed_job_request(
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
    secret: bytes,
    cache: ReplayCache,
    allowed_node_ids: set[str],
    now: float | None = None,
) -> dict[str, Any]:
    if len(body) > MAX_BODY_BYTES:
        raise ConnectorError("body_too_large", status=413)
    require_json_content(headers)
    timestamp = headers.get("x-esp-lab-timestamp", "")
    nonce = headers.get("x-esp-lab-nonce", "")
    idempotency_key = headers.get("idempotency-key", "")
    signature = headers.get("x-esp-lab-signature", "")
    if not timestamp or not nonce or not idempotency_key or not signature:
        raise ConnectorError("missing_auth", status=401)
    current = time.time() if now is None else now
    try:
        request_time = int(timestamp)
    except ValueError as exc:
        raise ConnectorError("invalid_timestamp") from exc
    if abs(current - request_time) > MAX_CLOCK_SKEW_SECONDS:
        raise ConnectorError("stale_timestamp", status=401)
    digest = body_hash(body)
    verify_signature(
        secret,
        signature,
        version=CONNECTOR_VERSION,
        method=method,
        path=path,
        timestamp=timestamp,
        nonce=nonce,
        idempotency_key=idempotency_key,
        body_sha256=digest,
    )
    cache.check(nonce=nonce, idempotency_key=idempotency_key, body_hash=digest, now=current)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectorError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise ConnectorError("invalid_json")
    job = validate_connector_job(payload)
    if job["node_id"] not in allowed_node_ids:
        raise ConnectorError("unknown_node", status=403)
    return job


def validate_connector_job(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema",
        "control_plane_id",
        "node_id",
        "endpoint_kind",
        "adapter_kind",
        "operation",
        "device_ref",
        "timeout_seconds",
        "idempotency_key",
        "execution_mode",
        "private_salt",
        "baud",
        "max_bytes",
        "expected_family",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise ConnectorError("unknown_field")
    if payload.get("schema") != f"{CONNECTOR_VERSION}.job":
        raise ConnectorError("invalid_schema")
    job = dict(payload)
    job["schema"] = "skeleton.home_edge.esp_lab.v1.job"
    safe = validate_job(job)
    if safe["endpoint_kind"] != "windows_workstation_connector" or safe["adapter_kind"] != "windows_com":
        raise ConnectorError("wrong_endpoint")
    return safe


def execute_connector_job(
    job: dict[str, Any],
    *,
    startup_allows_read_only: bool,
    adapter: Any = None,
    serial_adapter: Any = None,
    executable_finder: Any = None,
    esptool_command: str | list[str] | None = None,
) -> dict[str, Any]:
    if job["execution_mode"] == "read_only" and not startup_allows_read_only:
        raise ConnectorError("execution_not_enabled", status=403)
    observation, receipt = inspect_job(
        job,
        execute_read_only=startup_allows_read_only,
        adapter=adapter,
        serial_adapter=serial_adapter,
        executable_finder=executable_finder if executable_finder is not None else (lambda _: None),
        esptool_command=esptool_command,
    )
    return {"observation": observation, "receipt": receipt}


def signed_response(secret: bytes, payload: dict[str, Any], *, request_idempotency_key: str, status: int = 200) -> SignedResponse:
    body = canonical_body(payload)
    timestamp = str(int(time.time()))
    nonce = hashlib.sha256(body + timestamp.encode("ascii")).hexdigest()[:32]
    headers = {
        "content-type": "application/json",
        "x-esp-lab-timestamp": timestamp,
        "x-esp-lab-nonce": nonce,
        "idempotency-key": request_idempotency_key,
    }
    headers["x-esp-lab-signature"] = sign(
        secret,
        version=CONNECTOR_VERSION,
        method="RESPONSE",
        path=JOB_PATH,
        timestamp=timestamp,
        nonce=nonce,
        idempotency_key=request_idempotency_key,
        body_sha256=body_hash(body),
    )
    return SignedResponse(status=status, headers=headers, body=body)


def verify_signed_response(
    *,
    secret: bytes,
    response: SignedResponse,
    expected_idempotency_key: str,
    expected_node_id: str,
) -> dict[str, Any]:
    if response.headers.get("idempotency-key") != expected_idempotency_key:
        raise ConnectorError("correlation_mismatch")
    verify_signature(
        secret,
        response.headers.get("x-esp-lab-signature", ""),
        version=CONNECTOR_VERSION,
        method="RESPONSE",
        path=JOB_PATH,
        timestamp=response.headers.get("x-esp-lab-timestamp", ""),
        nonce=response.headers.get("x-esp-lab-nonce", ""),
        idempotency_key=expected_idempotency_key,
        body_sha256=body_hash(response.body),
    )
    payload = json.loads(response.body.decode("utf-8"))
    if payload.get("observation", {}).get("node_id") != expected_node_id:
        raise ConnectorError("wrong_node")
    return payload


def build_signed_request(*, secret: bytes, job: dict[str, Any], timestamp: int, nonce: str) -> tuple[bytes, dict[str, str]]:
    body = canonical_body(job)
    idem = str(job["idempotency_key"])
    headers = {
        "content-type": "application/json",
        "x-esp-lab-timestamp": str(timestamp),
        "x-esp-lab-nonce": nonce,
        "idempotency-key": idem,
    }
    headers["x-esp-lab-signature"] = sign(
        secret,
        version=CONNECTOR_VERSION,
        method="POST",
        path=JOB_PATH,
        timestamp=str(timestamp),
        nonce=nonce,
        idempotency_key=idem,
        body_sha256=body_hash(body),
    )
    return body, headers


def controller_dispatch(
    *,
    url: str,
    ca_cert: str | None,
    pinned_cert_sha256: str | None,
    secret: bytes,
    job: dict[str, Any],
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    if not ca_cert and not pinned_cert_sha256:
        raise ConnectorError("tls_verification_required")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ConnectorError("https_required")
    context = ssl.create_default_context(cafile=ca_cert) if ca_cert else ssl.create_default_context()
    body, headers = build_signed_request(secret=secret, job=job, timestamp=int(time.time()), nonce=hashlib.sha256(body_hash(canonical_body(job)).encode()).hexdigest()[:32])
    conn = http.client.HTTPSConnection(parsed.hostname or "", parsed.port or 443, context=context, timeout=timeout_seconds)
    conn.request("POST", parsed.path or JOB_PATH, body=body, headers=headers)
    raw = conn.getresponse()
    der_cert = conn.sock.getpeercert(binary_form=True) if conn.sock else None
    if pinned_cert_sha256 and hashlib.sha256(der_cert or b"").hexdigest().lower() != pinned_cert_sha256.lower():
        raise ConnectorError("certificate_pin_mismatch")
    response_body = raw.read(MAX_BODY_BYTES + 1)
    if len(response_body) > MAX_BODY_BYTES:
        raise ConnectorError("body_too_large")
    response = SignedResponse(raw.status, {k.lower(): v for k, v in raw.getheaders()}, response_body)
    return verify_signed_response(secret=secret, response=response, expected_idempotency_key=job["idempotency_key"], expected_node_id=job["node_id"])


class ConnectorHandler(http.server.BaseHTTPRequestHandler):
    server: "ConnectorHTTPServer"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path != HEALTH_PATH:
            self._send_json(404, {"error": "not_found"})
            return
        self._send_json(
            200,
            {
                "schema": f"{CONNECTOR_VERSION}.health",
                "node_id": self.server.config.node_id,
                "capabilities": {
                    "endpoint_kind": "windows_workstation_connector",
                    "adapter_kind": "windows_com",
                    "execution_enabled": self.server.config.allow_read_only_execution,
                },
            },
        )

    def do_POST(self) -> None:
        try:
            if self.path != JOB_PATH:
                raise ConnectorError("not_found", status=404)
            length = int(self.headers.get("content-length", "0"))
            if length > MAX_BODY_BYTES:
                raise ConnectorError("body_too_large", status=413)
            body = self.rfile.read(length)
            headers = {k.lower(): v for k, v in self.headers.items()}
            job = parse_signed_job_request(
                method="POST",
                path=self.path,
                headers=headers,
                body=body,
                secret=self.server.config.shared_secret,
                cache=self.server.cache,
                allowed_node_ids=self.server.config.allowed_node_ids,
            )
            payload = execute_connector_job(job, startup_allows_read_only=self.server.config.allow_read_only_execution)
            response = signed_response(self.server.config.shared_secret, payload, request_idempotency_key=job["idempotency_key"])
            self.send_response(response.status)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response.body)
        except ConnectorError as exc:
            self._send_json(exc.status, {"error": exc.code})
        except EspLabError:
            self._send_json(400, {"error": "invalid_job"})

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = canonical_body(payload)
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ConnectorHTTPServer(http.server.ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler: type[ConnectorHandler], config: ConnectorConfig) -> None:
        super().__init__(server_address, handler)
        self.config = config
        self.cache = ReplayCache()


def build_server(config: ConnectorConfig) -> ConnectorHTTPServer:
    server = ConnectorHTTPServer((config.bind_host, config.port), ConnectorHandler, config)
    if config.tls_cert and config.tls_key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(config.tls_cert, config.tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Home Edge ESP Lab Windows connector")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--node-id", required=True)
    common.add_argument("--secret-file", required=True)
    validate = sub.add_parser("validate-config", parents=[common])
    validate.add_argument("--bind-host", default="127.0.0.1")
    validate.add_argument("--tls-cert")
    validate.add_argument("--tls-key")
    validate.add_argument("--allow-lan", action="store_true")
    validate.add_argument("--allow-node-id", action="append", default=[])
    sub.add_parser("capabilities", parents=[common])
    serve = sub.add_parser("serve", parents=[common])
    serve.add_argument("--bind-host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=9443)
    serve.add_argument("--tls-cert")
    serve.add_argument("--tls-key")
    serve.add_argument("--allow-lan", action="store_true")
    serve.add_argument("--allow-node-id", action="append", default=[])
    serve.add_argument("--enable-read-only-execution", action="store_true")
    dispatch = sub.add_parser("dispatch-windows-job")
    dispatch.add_argument("--url", required=True)
    dispatch.add_argument("--job", required=True)
    dispatch.add_argument("--secret-file", required=True)
    dispatch.add_argument("--ca-cert")
    dispatch.add_argument("--pinned-cert-sha256")
    dispatch.add_argument("--private-out", required=True)
    dispatch.add_argument("--receipt-out", required=True)
    dispatch.add_argument("--allowed-root", action="append", default=None)
    args = parser.parse_args(argv)
    if args.command == "capabilities":
        _ = load_secret_file(args.secret_file)
        print(json.dumps({"endpoint_kind": "windows_workstation_connector", "adapter_kind": "windows_com", "default_execution": "plan"}, sort_keys=True))
        return 0
    if args.command == "dispatch-windows-job":
        payload = json.loads(Path(args.job).read_text(encoding="utf-8"))
        result = controller_dispatch(url=args.url, ca_cert=args.ca_cert, pinned_cert_sha256=args.pinned_cert_sha256, secret=load_secret_file(args.secret_file), job=payload)
        roots = [Path(root) for root in (args.allowed_root or [Path(args.private_out).parent, Path(args.receipt_out).parent])]
        write_json_private(validate_output_target(args.private_out, allowed_roots=roots), result["observation"])
        write_json_private(validate_output_target(args.receipt_out, allowed_roots=roots), result["receipt"])
        print(json.dumps({"status": result["receipt"]["aggregate"]}, sort_keys=True))
        return 0
    config = ConnectorConfig(
        node_id=args.node_id,
        shared_secret=load_secret_file(args.secret_file),
        bind_host=args.bind_host,
        tls_cert=getattr(args, "tls_cert", None),
        tls_key=getattr(args, "tls_key", None),
        allow_lan=getattr(args, "allow_lan", False),
        allow_read_only_execution=getattr(args, "enable_read_only_execution", False),
        allowed_node_ids=set(getattr(args, "allow_node_id", []) or [args.node_id]),
    )
    if args.command == "validate-config":
        print(json.dumps({"status": "ok", "bind_host": config.bind_host, "execution": "enabled" if config.allow_read_only_execution else "plan_only"}, sort_keys=True))
        return 0
    server = build_server(config)
    server.serve_forever()
    return 0
