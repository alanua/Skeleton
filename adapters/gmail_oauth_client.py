from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Final
from urllib import parse, request
from urllib.error import HTTPError, URLError


GMAIL_READONLY_SCOPE: Final = "https://www.googleapis.com/auth/gmail.readonly"
DEFAULT_TOKEN_URI: Final = "https://oauth2.googleapis.com/token"
DEFAULT_AUTH_URI: Final = "https://accounts.google.com/o/oauth2/v2/auth"
DEFAULT_GMAIL_API_ROOT: Final = "https://gmail.googleapis.com/gmail/v1"

_TOKEN_REFRESH_SKEW_SECONDS: Final = 60
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+,-]{0,255}$")


class GmailOAuthError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class GmailCredentialBundle:
    account_ref: str
    client_id: str
    client_secret: str
    refresh_token: str
    scopes: tuple[str, ...]
    token_uri: str = DEFAULT_TOKEN_URI
    access_token: str | None = None
    expires_at: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, account_ref: str) -> "GmailCredentialBundle":
        if not isinstance(value, Mapping):
            raise GmailOAuthError("GMAIL_CREDENTIAL_INVALID", "credential bundle must be an object")
        scopes = value.get("scopes")
        if not isinstance(scopes, list) or GMAIL_READONLY_SCOPE not in scopes:
            raise GmailOAuthError("GMAIL_OAUTH_SCOPE_INVALID", "gmail readonly scope is required")
        client_id = _required_secret(value.get("client_id"), "client_id")
        client_secret = _required_secret(value.get("client_secret"), "client_secret")
        refresh_token = _required_secret(value.get("refresh_token"), "refresh_token")
        token_uri = value.get("token_uri", DEFAULT_TOKEN_URI)
        if not isinstance(token_uri, str) or not token_uri.startswith("https://"):
            raise GmailOAuthError("GMAIL_CREDENTIAL_INVALID", "token_uri must be https")
        access_token = value.get("access_token")
        if access_token is not None and not isinstance(access_token, str):
            raise GmailOAuthError("GMAIL_CREDENTIAL_INVALID", "access_token must be text")
        expires_at = value.get("expires_at", 0)
        if isinstance(expires_at, bool) or not isinstance(expires_at, int) or expires_at < 0:
            raise GmailOAuthError("GMAIL_CREDENTIAL_INVALID", "expires_at must be non-negative")
        return cls(
            account_ref=account_ref,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            scopes=tuple(str(item) for item in scopes),
            token_uri=token_uri,
            access_token=access_token,
            expires_at=expires_at,
        )

    def to_private_mapping(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema": "skeleton.gmail_oauth_credential.v1",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "scopes": list(self.scopes),
            "token_uri": self.token_uri,
            "expires_at": self.expires_at,
        }
        if self.access_token:
            value["access_token"] = self.access_token
        return value


class GmailCredentialStore:
    """Loads per-account OAuth material from a local private directory."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else default_credential_root()

    def path_for(self, account_ref: str) -> Path:
        _validate_account_ref(account_ref)
        digest = hashlib.sha256(account_ref.encode("utf-8")).hexdigest()
        return self.root / f"gmail-{digest}.json"

    def load(self, account_ref: str) -> GmailCredentialBundle:
        path = self.path_for(account_ref)
        if not path.exists():
            raise GmailOAuthError("GMAIL_CREDENTIAL_MISSING", "gmail credential bundle is missing")
        _assert_private_file(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GmailOAuthError("GMAIL_CREDENTIAL_INVALID", "gmail credential bundle is invalid") from exc
        return GmailCredentialBundle.from_mapping(raw, account_ref=account_ref)

    def save(self, bundle: GmailCredentialBundle) -> Path:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _assert_private_directory(self.root)
        path = self.path_for(bundle.account_ref)
        tmp = path.with_suffix(".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(tmp, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(bundle.to_private_mapping(), handle, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        return path


class GmailOAuthReadOnlyClient:
    def __init__(
        self,
        *,
        account_ref: str,
        credential_store: GmailCredentialStore | None = None,
        http_request: Callable[..., tuple[int, Mapping[str, str], bytes]] | None = None,
        clock: Callable[[], float] | None = None,
        api_root: str = DEFAULT_GMAIL_API_ROOT,
    ) -> None:
        self.account_ref = account_ref
        self.credential_store = credential_store or GmailCredentialStore()
        self._http_request = http_request or _urllib_request
        self._clock = clock or time.time
        self._api_root = api_root.rstrip("/")

    def list_messages(self, *, query: str, max_results: int) -> tuple[Mapping[str, Any], ...]:
        if isinstance(max_results, bool) or max_results <= 0 or max_results > 100:
            raise GmailOAuthError("GMAIL_REQUEST_INVALID", "max_results must be 1..100")
        token = self._access_token()
        params = parse.urlencode({"q": query, "maxResults": str(max_results)})
        status, _, body = self._http_request(
            f"{self._api_root}/users/me/messages?{params}",
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        response = _decode_json_response(status, body, reason="GMAIL_MESSAGES_LIST_FAILED")
        messages = response.get("messages", ())
        if not isinstance(messages, list):
            raise GmailOAuthError("GMAIL_MESSAGES_LIST_FAILED", "gmail list response was invalid")
        fetched: list[Mapping[str, Any]] = []
        for item in messages[:max_results]:
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                fetched.append(self.get_message(item["id"]))
        return tuple(fetched)

    def get_message(self, message_id: str) -> Mapping[str, Any]:
        if not isinstance(message_id, str) or not message_id:
            raise GmailOAuthError("GMAIL_REQUEST_INVALID", "message_id is required")
        token = self._access_token()
        params = parse.urlencode(
            (
                ("format", "metadata"),
                ("metadataHeaders", "Subject"),
                ("metadataHeaders", "From"),
            )
        )
        status, _, body = self._http_request(
            f"{self._api_root}/users/me/messages/{parse.quote(message_id, safe='')}?{params}",
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        response = _decode_json_response(status, body, reason="GMAIL_MESSAGES_GET_FAILED")
        if not isinstance(response, Mapping):
            raise GmailOAuthError("GMAIL_MESSAGES_GET_FAILED", "gmail get response was invalid")
        return response

    def _access_token(self) -> str:
        bundle = self.credential_store.load(self.account_ref)
        now = int(self._clock())
        if bundle.access_token and bundle.expires_at > now + _TOKEN_REFRESH_SKEW_SECONDS:
            return bundle.access_token
        refreshed = self._refresh(bundle)
        self.credential_store.save(refreshed)
        if not refreshed.access_token:
            raise GmailOAuthError("GMAIL_OAUTH_REFRESH_FAILED", "refresh did not return access token")
        return refreshed.access_token

    def _refresh(self, bundle: GmailCredentialBundle) -> GmailCredentialBundle:
        payload = parse.urlencode(
            {
                "client_id": bundle.client_id,
                "client_secret": bundle.client_secret,
                "refresh_token": bundle.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        status, _, body = self._http_request(
            bundle.token_uri,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=payload,
        )
        response = _decode_json_response(status, body, reason="GMAIL_OAUTH_REFRESH_FAILED")
        access_token = response.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise GmailOAuthError("GMAIL_OAUTH_REFRESH_FAILED", "refresh response missing access token")
        expires_in = response.get("expires_in", 3600)
        if isinstance(expires_in, bool) or not isinstance(expires_in, int) or expires_in <= 0:
            raise GmailOAuthError("GMAIL_OAUTH_REFRESH_FAILED", "refresh response expires_in invalid")
        scope_text = response.get("scope")
        scopes = tuple(str(scope_text).split()) if isinstance(scope_text, str) and scope_text else bundle.scopes
        if GMAIL_READONLY_SCOPE not in scopes:
            raise GmailOAuthError("GMAIL_OAUTH_SCOPE_INVALID", "refreshed token lacks readonly scope")
        return GmailCredentialBundle(
            account_ref=bundle.account_ref,
            client_id=bundle.client_id,
            client_secret=bundle.client_secret,
            refresh_token=bundle.refresh_token,
            scopes=scopes,
            token_uri=bundle.token_uri,
            access_token=access_token,
            expires_at=int(self._clock()) + expires_in,
        )


def build_authorization_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    _required_secret(client_id, "client_id")
    if not isinstance(redirect_uri, str) or not redirect_uri:
        raise GmailOAuthError("GMAIL_ONBOARDING_INVALID", "redirect_uri is required")
    return DEFAULT_AUTH_URI + "?" + parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GMAIL_READONLY_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )


def exchange_authorization_code(
    *,
    account_ref: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    credential_store: GmailCredentialStore | None = None,
    http_request: Callable[..., tuple[int, Mapping[str, str], bytes]] | None = None,
    clock: Callable[[], float] | None = None,
) -> Path:
    _validate_account_ref(account_ref)
    _required_secret(code, "code")
    store = credential_store or GmailCredentialStore()
    requester = http_request or _urllib_request
    now = int((clock or time.time)())
    payload = parse.urlencode(
        {
            "client_id": _required_secret(client_id, "client_id"),
            "client_secret": _required_secret(client_secret, "client_secret"),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    status, _, body = requester(
        DEFAULT_TOKEN_URI,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=payload,
    )
    response = _decode_json_response(status, body, reason="GMAIL_OAUTH_EXCHANGE_FAILED")
    refresh_token = response.get("refresh_token")
    access_token = response.get("access_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise GmailOAuthError("GMAIL_OAUTH_EXCHANGE_FAILED", "exchange response missing refresh token")
    scopes = tuple(str(response.get("scope") or GMAIL_READONLY_SCOPE).split())
    if GMAIL_READONLY_SCOPE not in scopes:
        raise GmailOAuthError("GMAIL_OAUTH_SCOPE_INVALID", "authorized token lacks readonly scope")
    expires_in = response.get("expires_in", 0)
    expires_at = now + expires_in if isinstance(expires_in, int) and expires_in > 0 else 0
    bundle = GmailCredentialBundle(
        account_ref=account_ref,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        scopes=scopes,
        access_token=access_token if isinstance(access_token, str) else None,
        expires_at=expires_at,
    )
    return store.save(bundle)


def default_credential_root() -> Path:
    raw = os.environ.get("SKELETON_GMAIL_CREDENTIAL_ROOT")
    if raw:
        return Path(raw)
    return Path.home() / ".local" / "state" / "skeleton" / "mail" / "gmail"


def _decode_json_response(status: int, body: bytes, *, reason: str) -> Mapping[str, Any]:
    if status in {400, 401} and reason == "GMAIL_OAUTH_REFRESH_FAILED":
        raise GmailOAuthError("GMAIL_CREDENTIAL_REVOKED", "gmail refresh credential rejected")
    if status < 200 or status >= 300:
        raise GmailOAuthError(reason, "gmail request failed")
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GmailOAuthError(reason, "gmail response was not json") from exc
    if not isinstance(decoded, Mapping):
        raise GmailOAuthError(reason, "gmail response was not an object")
    return decoded


def _urllib_request(
    url: str,
    *,
    method: str,
    headers: Mapping[str, str] | None = None,
    data: bytes | None = None,
) -> tuple[int, Mapping[str, str], bytes]:
    req = request.Request(url, method=method, headers=dict(headers or {}), data=data)
    try:
        with request.urlopen(req, timeout=30) as response:  # nosec: operator runtime only
            return response.status, dict(response.headers.items()), response.read(1024 * 1024)
    except HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read(1024 * 1024)
    except URLError as exc:
        raise GmailOAuthError("GMAIL_NETWORK_UNAVAILABLE", "gmail network unavailable") from exc


def _assert_private_directory(path: Path) -> None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise GmailOAuthError("GMAIL_CREDENTIAL_MISSING", "credential root is missing") from exc
    if mode & 0o077:
        raise GmailOAuthError("GMAIL_CREDENTIAL_PERMISSIONS_INVALID", "credential root is not private")


def _assert_private_file(path: Path) -> None:
    _assert_private_directory(path.parent)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise GmailOAuthError("GMAIL_CREDENTIAL_MISSING", "credential bundle is missing") from exc
    if mode & 0o077:
        raise GmailOAuthError("GMAIL_CREDENTIAL_PERMISSIONS_INVALID", "credential bundle is not private")


def _validate_account_ref(value: str) -> str:
    if not isinstance(value, str) or _SAFE_REF_RE.fullmatch(value) is None:
        raise GmailOAuthError("GMAIL_ACCOUNT_REF_INVALID", "account_ref must be opaque safe ref")
    return value


def _required_secret(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GmailOAuthError("GMAIL_CREDENTIAL_INVALID", f"{field} is required")
    return value
