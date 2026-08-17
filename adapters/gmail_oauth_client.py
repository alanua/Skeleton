from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"


class GmailOAuthError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class GmailOAuthBundle:
    client_id: str
    client_secret: str
    refresh_token: str
    scopes: tuple[str, ...]

    @classmethod
    def from_json(cls, raw: str) -> "GmailOAuthBundle":
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise GmailOAuthError("GMAIL_OAUTH_BUNDLE_MALFORMED") from exc
        if not isinstance(value, Mapping):
            raise GmailOAuthError("GMAIL_OAUTH_BUNDLE_MALFORMED")
        if set(value) - {"client_id", "client_secret", "refresh_token", "scopes"}:
            raise GmailOAuthError("GMAIL_OAUTH_BUNDLE_UNKNOWN_FIELDS")
        client_id = value.get("client_id")
        client_secret = value.get("client_secret")
        refresh_token = value.get("refresh_token")
        scopes = value.get("scopes")
        if not all(isinstance(item, str) and item for item in (client_id, client_secret, refresh_token)):
            raise GmailOAuthError("GMAIL_OAUTH_BUNDLE_MALFORMED")
        if not isinstance(scopes, list) or not scopes or not all(isinstance(item, str) and item for item in scopes):
            raise GmailOAuthError("GMAIL_OAUTH_BUNDLE_MALFORMED")
        normalized_scopes = tuple(sorted(set(scopes)))
        if GMAIL_READONLY_SCOPE not in normalized_scopes:
            raise GmailOAuthError("GMAIL_OAUTH_SCOPE_INSUFFICIENT")
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            scopes=normalized_scopes,
        )


Transport = Callable[[str, str, bytes | None, Mapping[str, str]], Mapping[str, Any]]


def _decode_json_response(raw: bytes) -> Mapping[str, Any]:
    if len(raw) > 2_000_000:
        raise GmailOAuthError("GMAIL_PROVIDER_RESPONSE_TOO_LARGE")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GmailOAuthError("GMAIL_PROVIDER_RESPONSE_MALFORMED") from exc
    if not isinstance(value, Mapping):
        raise GmailOAuthError("GMAIL_PROVIDER_RESPONSE_MALFORMED")
    return value


def _network_transport(
    method: str,
    url: str,
    body: bytes | None,
    headers: Mapping[str, str],
) -> Mapping[str, Any]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(2_000_001)
    except urllib.error.HTTPError as exc:
        raw = exc.read(2_000_001)
        if url == GOOGLE_TOKEN_URL:
            try:
                value = _decode_json_response(raw)
            except GmailOAuthError:
                raise GmailOAuthError("GMAIL_OAUTH_REFRESH_FAILED") from None
            if value.get("error") == "invalid_grant":
                return {"error": "invalid_grant"}
            raise GmailOAuthError("GMAIL_OAUTH_REFRESH_FAILED") from None
        if exc.code in {401, 403}:
            raise GmailOAuthError("GMAIL_AUTHORIZATION_FAILED") from None
        raise GmailOAuthError("GMAIL_PROVIDER_REQUEST_FAILED") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GmailOAuthError("GMAIL_PROVIDER_UNAVAILABLE") from exc
    return _decode_json_response(raw)


class GmailOAuthClient:
    """Minimal Gmail read-only client. It exposes GET/list metadata operations only."""

    def __init__(self, bundle: GmailOAuthBundle, *, transport: Transport | None = None) -> None:
        self._bundle = bundle
        self._transport = transport or _network_transport

    def _access_token(self) -> str:
        body = urllib.parse.urlencode(
            {
                "client_id": self._bundle.client_id,
                "client_secret": self._bundle.client_secret,
                "refresh_token": self._bundle.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("ascii")
        response = self._transport(
            "POST",
            GOOGLE_TOKEN_URL,
            body,
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        token = response.get("access_token")
        if not isinstance(token, str) or not token:
            if response.get("error") == "invalid_grant":
                raise GmailOAuthError("GMAIL_OAUTH_REVOKED")
            raise GmailOAuthError("GMAIL_OAUTH_REFRESH_FAILED")
        return token

    def list_messages(self, *, query: str, max_results: int) -> tuple[Mapping[str, Any], ...]:
        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= 100:
            raise GmailOAuthError("GMAIL_MAX_RESULTS_INVALID")
        if not isinstance(query, str) or len(query) > 512:
            raise GmailOAuthError("GMAIL_QUERY_INVALID")
        token = self._access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        params = urllib.parse.urlencode({"q": query, "maxResults": max_results})
        listing = self._transport(
            "GET",
            f"{GMAIL_API_BASE}/users/me/messages?{params}",
            None,
            headers,
        )
        rows = listing.get("messages", [])
        if not isinstance(rows, list):
            raise GmailOAuthError("GMAIL_PROVIDER_RESPONSE_MALFORMED")
        fetched: list[Mapping[str, Any]] = []
        for row in rows[:max_results]:
            if not isinstance(row, Mapping) or not isinstance(row.get("id"), str) or not row["id"]:
                raise GmailOAuthError("GMAIL_PROVIDER_RESPONSE_MALFORMED")
            message_id = urllib.parse.quote(row["id"], safe="")
            message = self._transport(
                "GET",
                f"{GMAIL_API_BASE}/users/me/messages/{message_id}?format=metadata",
                None,
                headers,
            )
            fetched.append(message)
        return tuple(fetched)
