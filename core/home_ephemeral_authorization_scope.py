"""Pure, fail-closed view gate for Home's existing ephemeral auth broker.

This module does NOT create a new secret store or HTTP endpoint. The deployed
Home broker currently holds operator-visible one-time verification codes but
does not scope them to individual authenticated sessions. A future reviewed
adapter may pass existing broker rows through this function after it has bound
each new row to server-verified owner, device, session and random flow nonce.

Only an authenticated server-side request handler may construct RequestScope.
Do not build it from client-supplied IP or X-Forwarded-* headers. Proxy trust,
write/expiry cleanup, device-code custody and token storage are separate gates.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import hmac
import re
from typing import Any


_ID = re.compile(r"^[A-Za-z0-9_./:@-]{1,160}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


@dataclass(frozen=True, slots=True, repr=False)
class RequestScope:
    owner_id: str
    device_id: str
    session_id: str
    flow_nonce: str
    role: str
    authenticated: bool
    encrypted_transport: bool
    lost_device: bool = False

    def __repr__(self) -> str:
        return "RequestScope(<redacted>)"


@dataclass(frozen=True, slots=True)
class AuthorizationView:
    service: str
    label: str
    code: str = field(repr=False)
    verification_url: str = field(repr=False)
    expires_at: int = 0

    def to_operator_payload(self) -> dict[str, str | int]:
        return {
            "service": self.service,
            "label": self.label,
            "code": self.code,
            "verification_url": self.verification_url,
            "expires_at": self.expires_at,
            "status": "active",
        }


def _trusted_scope(scope: RequestScope) -> bool:
    if not isinstance(scope, RequestScope):
        return False
    if not (scope.authenticated and scope.encrypted_transport):
        return False
    if scope.role != "full" or scope.lost_device:
        return False
    if not all(isinstance(x, str) and _ID.fullmatch(x) for x in (
        scope.owner_id, scope.device_id, scope.session_id
    )):
        return False
    return isinstance(scope.flow_nonce, str) and bool(_NONCE.fullmatch(scope.flow_nonce))


def _same(left: object, right: str) -> bool:
    """Compare bounded operator/session claims without echoing either claim."""
    if not isinstance(left, str) or len(left) > 160 or not left.isascii():
        return False
    return hmac.compare_digest(left, right)


def scoped_ephemeral_views(
    rows: Iterable[Mapping[str, Any]],
    scope: RequestScope,
    *,
    now: int,
    verification_urls: Mapping[str, tuple[str, ...]],
    max_results: int = 8,
) -> tuple[AuthorizationView, ...]:
    """Filter existing broker records; NEVER return unowned legacy rows.

    Every row must have owner_id, device_id, session_id and flow_nonce from the
    server's creation of the authorization flow. Service-specific permitted
    verification URLs are fixed and registered separately, never arbitrary
    caller-supplied URL destinations. No device_code or OAuth token is returned.

    A session binder must enforce nonce issuance/replay/one-time consumption.
    The function is read-only and does not persist anything or clear expired
    records; the existing #4306 broker retains that responsibility.
    """
    if not _trusted_scope(scope):
        return ()
    if type(now) is not int or now < 0 or not (1 <= max_results <= 32):
        return ()
    if not isinstance(verification_urls, Mapping):
        return ()

    allowed = {
        str(service): set(urls)
        for service, urls in verification_urls.items()
        if isinstance(service, str)
        and isinstance(urls, tuple)
        and all(isinstance(u, str) and u.startswith("https://") for u in urls)
    }
    result: list[AuthorizationView] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if not all(_same(row.get(key), expected) for key, expected in (
            ("owner_id", scope.owner_id),
            ("device_id", scope.device_id),
            ("session_id", scope.session_id),
            ("flow_nonce", scope.flow_nonce),
        )):
            continue
        service = row.get("service")
        label = row.get("label")
        code = row.get("code")
        url = row.get("verification_url")
        if not isinstance(service, str) or service not in allowed:
            continue
        if not isinstance(url, str) or url not in allowed[service]:
            continue
        if row.get("status") != "active":
            continue
        if not isinstance(code, str) or not 2 <= len(code) <= 256:
            continue
        try:
            expires_at = int(row.get("expires_at"))
        except (TypeError, ValueError, OverflowError):
            continue
        if expires_at <= now or expires_at > now + 86400:
            continue
        if not isinstance(label, str):
            label = service
        result.append(AuthorizationView(
            service=service,
            label=label[:120],
            code=code,
            verification_url=url,
            expires_at=expires_at,
        ))
        if len(result) >= max_results:
            break
    return tuple(result)
