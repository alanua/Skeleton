"""Bounded LAN HTTP adapter, executed only *inside* approved Home Edge operations.

The caller provides a vetted operation definition, not arbitrary chat-authored URLs.
Credentials are resolved on-node through Skeleton's SecretStoreGate and never
printed or placed in the Home Edge request envelope.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import ipaddress
import urllib.parse
import urllib.request
from typing import Mapping, Protocol

from core.secret_store import SecretReference, SecretResolutionContext, SecretStoreGate


class DeviceHttpError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceHttpOperation:
    device_id: str
    ip_address: str
    path: str
    method: str
    body: bytes = b""
    auth: str = "none"
    secret_ref: SecretReference | None = None
    timeout_seconds: int = 15
    max_response_bytes: int = 16384


@dataclass(frozen=True)
class DeviceHttpOutcome:
    status_code: int
    accepted: bool
    response_size: int


def _checked_operation(op: DeviceHttpOperation) -> str:
    if not isinstance(op, DeviceHttpOperation):
        raise DeviceHttpError("invalid_operation")
    try:
        addr = ipaddress.ip_address(op.ip_address)
    except ValueError:
        raise DeviceHttpError("invalid_ip") from None
    if addr.version != 4 or not addr.is_private or addr.is_loopback or addr.is_link_local:
        raise DeviceHttpError("outside_trusted_lan")
    if op.method not in ("GET", "POST"):
        raise DeviceHttpError("method_not_allowed")
    if not op.path.startswith("/") or op.path.startswith("//") or any(
        c in op.path for c in ("\\", "\r", "\n", "#")
    ):
        raise DeviceHttpError("invalid_path")
    if urllib.parse.urlsplit(op.path).netloc:
        raise DeviceHttpError("invalid_path")
    if op.auth not in ("none", "basic"):
        raise DeviceHttpError("unsupported_auth")
    if (op.auth == "basic") != (op.secret_ref is not None):
        raise DeviceHttpError("auth_reference_mismatch")
    if not (1 <= op.timeout_seconds <= 30 and 1 <= op.max_response_bytes <= 65536):
        raise DeviceHttpError("bounds_exceeded")
    if len(op.body) > 4096 or (op.method == "GET" and op.body):
        raise DeviceHttpError("invalid_request_body")
    return "http://" + str(addr) + op.path


def perform_device_http(
    op: DeviceHttpOperation,
    *,
    secrets: SecretStoreGate,
    context: SecretResolutionContext,
    opener: urllib.request.OpenerDirector | None = None,
) -> DeviceHttpOutcome:
    url = _checked_operation(op)
    headers = {"User-Agent": "Skeleton-Device-Adapter/1", "Accept": "application/json,text/plain,*/*"}
    if op.auth == "basic":
        if context.machine_identity != "home-edge-01" or context.audience != op.device_id:
            raise DeviceHttpError("secret_context_out_of_scope")
        material = secrets.resolve(op.secret_ref, context)
        # Only this vetted adapter receives the sensitive value, in memory.
        auth_env = material.inject({}, "SKELETON_HTTP_PASSWORD")
        raw = (":" + auth_env["SKELETON_HTTP_PASSWORD"]).encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
        del raw, auth_env
    request = urllib.request.Request(url, data=op.body if op.method == "POST" else None, headers=headers, method=op.method)
    # No automatic redirects: never forward Authorization to another origin.
    class RejectRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise DeviceHttpError("redirect_rejected")
    active_opener = opener or urllib.request.build_opener(RejectRedirect)
    try:
        with active_opener.open(request, timeout=op.timeout_seconds) as reply:
            size = len(reply.read(op.max_response_bytes + 1))
            if size > op.max_response_bytes:
                raise DeviceHttpError("response_too_large")
            return DeviceHttpOutcome(reply.status, 200 <= reply.status < 300, size)
    except DeviceHttpError:
        raise
    except Exception:
        # Authentication, response bodies, and URLs must not reach audit stderr.
        raise DeviceHttpError("device_request_failed") from None
