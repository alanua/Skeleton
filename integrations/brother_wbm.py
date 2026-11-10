"""Inactive Brother WBM boundary; no inferred HTTP forms or live enrollment.

A reviewed browser/session implementation must be installed by the canonical
Home Edge executor before activation. This module has no network entry point.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import hmac
import re
import ssl
from typing import Protocol
from urllib.parse import urlsplit

from integrations.credential_runtime import consume_registered_material_credential


_DNS_NAME = re.compile(
    r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\Z"
)


@dataclass(frozen=True)
class BrotherTrustBinding:
    """Operator-enrolled device identity, never learned from the connection."""

    hostname: str
    certificate_sha256: str
    ca_file: str

    def tls_context(self) -> ssl.SSLContext:
        if (
            not isinstance(self.hostname, str)
            or not _DNS_NAME.fullmatch(self.hostname)
            or not isinstance(self.certificate_sha256, str)
            or len(self.certificate_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.certificate_sha256)
            or not isinstance(self.ca_file, str)
            or not self.ca_file.startswith("/")
        ):
            raise ValueError("brother_trust_binding_invalid")
        context = ssl.create_default_context(cafile=self.ca_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context


@dataclass(frozen=True)
class StatusEvidence:
    # No raw page content, cookies, credentials or arbitrary status text.
    url: str
    http_status: int
    model: str
    logout_control_present: bool
    login_form_present: bool
    redirected: bool = False


class WbmSession(Protocol):
    """Trusted in-process browser port, not a caller-controlled RPC surface.

    connect must verify hostname/chain using the supplied context and disable
    redirects; login must submit the rendered vendor Login control once, without
    tracing, persistence, logs or retries. The peer certificate is checked before
    login. Every navigation must stay on the enrolled origin without redirects.
    """

    def connect(self, origin: str, context: ssl.SSLContext) -> bytes: ...
    def login_and_read_status(self, password: str) -> StatusEvidence: ...
    def close(self) -> None: ...


def _authenticated(evidence: StatusEvidence, origin: str) -> bool:
    if not isinstance(evidence, StatusEvidence):
        return False
    parsed = urlsplit(evidence.url)
    return (
        parsed.scheme == "https"
        and parsed.netloc == urlsplit(origin).netloc
        and parsed.username is None
        and parsed.password is None
        and evidence.http_status == 200
        and evidence.redirected is False
        and evidence.model == "MFC-J4540DW"
        and evidence.logout_control_present is True
        and evidence.login_form_present is False
    )


def _read_authenticated_status(
    *,
    trust: BrotherTrustBinding,
    session: WbmSession,
    authority_environment: Mapping[str, str],
) -> dict[str, object]:
    """Internal seam for a future registered executor worker; not activated.

    Only constant receipts escape. In particular, exception text and rendered
    pages can contain credentials and must never enter executor output.
    """
    result: dict[str, object] = {"status": "BLOCKED", "reason": "STATUS_UNVERIFIED"}
    try:
        origin = "https://" + trust.hostname
        context = trust.tls_context()
        if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True:
            raise ValueError("brother_tls_verification_required")
        peer = session.connect(origin, context)
        if not isinstance(peer, bytes) or not peer:
            raise ValueError("brother_peer_certificate_required")
        if not hmac.compare_digest(hashlib.sha256(peer).hexdigest(), trust.certificate_sha256):
            raise ValueError("brother_device_trust_failed")
        authenticated = False

        def consume(password: str) -> None:
            nonlocal authenticated
            authenticated = _authenticated(session.login_and_read_status(password), origin)

        receipt = consume_registered_material_credential(
            service_id="brother-mfc-j4540dw",
            alias="wbm-admin",
            action_id="read-authenticated-status",
            consumer=consume,
            authority_environment=authority_environment,
        )
        if receipt.get("result", {}).get("status") == "USED" and authenticated:
            result = {"status": "OK", "authenticated": True, "model": "MFC-J4540DW"}
    except Exception:
        # Neither provider nor browser exception strings are public-safe.
        pass
    finally:
        try:
            session.close()
        except Exception:
            result = {"status": "BLOCKED", "reason": "SESSION_CLOSE_FAILED"}
    return result
