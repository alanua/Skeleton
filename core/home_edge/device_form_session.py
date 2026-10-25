"""Bounded form-based web session for approved device operations.

An existing SecretStoreGate supplies the password in process memory only.
No credentials are put in the Home Edge audit envelope. A freshly constructed
cookie jar is scoped to a single call; callers must verify login independently.
"""
from __future__ import annotations

from dataclasses import dataclass
from http.cookiejar import CookieJar
import ipaddress
import ssl
import urllib.parse
import urllib.request

from core.secret_store import SecretReference, SecretResolutionContext, SecretStoreGate


class DeviceSessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class FormSessionProfile:
    device_id: str
    ip_address: str
    login_path: str
    protected_path: str
    password_field: str
    hidden_fields: tuple[tuple[str, str], ...] = ()
    tls: bool = True
    authenticated_marker: bytes = b""
    max_response_bytes: int = 32768


def _path(p: str) -> str:
    if not isinstance(p, str) or not p.startswith("/") or p.startswith("//"):
        raise DeviceSessionError("invalid_path")
    parsed = urllib.parse.urlsplit(p)
    if parsed.scheme or parsed.netloc or parsed.fragment or any(ch in p for ch in ("\\", "\r", "\n")):
        raise DeviceSessionError("invalid_path")
    return p


def _base(profile: FormSessionProfile) -> str:
    try:
        addr = ipaddress.ip_address(profile.ip_address)
    except ValueError:
        raise DeviceSessionError("invalid_ip") from None
    if addr.version != 4 or not addr.is_private or addr.is_loopback or addr.is_link_local:
        raise DeviceSessionError("outside_lan")
    _path(profile.login_path)
    _path(profile.protected_path)
    if not profile.password_field.isidentifier() or len(profile.hidden_fields) > 8:
        raise DeviceSessionError("invalid_form")
    if not profile.authenticated_marker or len(profile.authenticated_marker) > 256:
        raise DeviceSessionError("authenticated_marker_required")
    if not 1 <= profile.max_response_bytes <= 65536:
        raise DeviceSessionError("invalid_response_bound")
    if any(not k.isidentifier() or len(k) > 64 or len(v) > 256 for k, v in profile.hidden_fields):
        raise DeviceSessionError("invalid_form_fields")
    return ("https" if profile.tls else "http") + "://" + str(addr)


def acquire_device_session(
    profile: FormSessionProfile,
    *,
    secret_ref: SecretReference,
    secrets: SecretStoreGate,
    context: SecretResolutionContext,
    opener: urllib.request.OpenerDirector | None = None,
) -> bool:
    base = _base(profile)
    if context.machine_identity != "home-edge-01" or context.audience != profile.device_id:
        raise DeviceSessionError("secret_context_out_of_scope")
    # TLS verification must use a provisioned device trust root; callers must
    # not disable certificate validation to bypass self-signed certificates.
    jar = CookieJar()
    class RejectRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise DeviceSessionError("redirect_requires_explicit_review")
    active_opener = opener or urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), RejectRedirect()
    )
    material = secrets.resolve(secret_ref, context)
    env = material.inject({}, "DEVICE_FORM_PASSWORD")
    fields = dict(profile.hidden_fields)
    fields[profile.password_field] = env.pop("DEVICE_FORM_PASSWORD")
    body = urllib.parse.urlencode(fields).encode("utf-8")
    del fields, env
    try:
        with active_opener.open(
            urllib.request.Request(
                base + profile.login_path,
                data=body,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ), timeout=15,
        ) as login:
            if len(login.read(profile.max_response_bytes + 1)) > profile.max_response_bytes:
                raise DeviceSessionError("oversized_login_response")
        with active_opener.open(base + profile.protected_path, timeout=15) as protected:
            raw = protected.read(profile.max_response_bytes + 1)
            if len(raw) > profile.max_response_bytes:
                raise DeviceSessionError("oversized_protected_response")
            # The profile cannot infer successful login solely from HTTP 200.
            return (protected.status == 200 and profile.authenticated_marker in raw
                    and b"Please&#32;Login" not in raw
                    and b"Please Login" not in raw
                    and b'id="LogBox"' not in raw)
    except DeviceSessionError:
        raise
    except Exception:
        raise DeviceSessionError("session_request_failed") from None
