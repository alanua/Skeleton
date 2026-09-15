#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import Mapping, Sequence
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


BITWARDEN_ACCESS_TOKEN_CREDENTIAL = "bitwarden-access-token"
GMAIL_SECRET_KEY = "skeleton/mail-gmail/acct:gmail-primary/oauth-readonly"
IDENTITY_TOKEN_URL = "https://identity.bitwarden.com/connect/token"
REFERENCE_INDEX_CREDENTIAL = "skeleton-secret-reference-index"
REFERENCE_INDEX_OUTPUT = f"/etc/credstore.encrypted/{REFERENCE_INDEX_CREDENTIAL}"
REFERENCE_INDEX_SCHEMA = "skeleton.secret_reference_index.v1"
SERVICE_ID = "mail-gmail"
ALIAS = "acct:gmail-primary"
API_SCOPE = "api.secrets"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_TOKEN_PART_RE = re.compile(r"^[A-Za-z0-9_./:=+@-]{8,4096}$")


class BootstrapError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Gmail Bitwarden reference index")
    parser.add_argument("--identity-url", default=IDENTITY_TOKEN_URL)
    parser.add_argument("--credentials-directory", default=os.environ.get("CREDENTIALS_DIRECTORY", ""))
    parser.add_argument("--index-output", default=REFERENCE_INDEX_OUTPUT)
    args = parser.parse_args(argv)

    try:
        if args.identity_url != IDENTITY_TOKEN_URL:
            raise BootstrapError("IDENTITY_URL_NOT_ALLOWLISTED")
        credentials_dir = _credential_directory(args.credentials_directory)
        machine_token = _read_private_credential(credentials_dir, BITWARDEN_ACCESS_TOKEN_CREDENTIAL)
        client_id, client_secret = parse_machine_access_token(machine_token)
        token_response = request_identity_token(
            identity_url=args.identity_url,
            client_id=client_id,
            client_secret=client_secret,
        )
        organization_id = validate_identity_token_response(
            token_response,
            identity_url=args.identity_url,
            client_id=client_id,
            now=int(time.time()),
        )
        reference_id, match_count_class = discover_gmail_secret_reference(
            machine_token=machine_token,
            organization_id=organization_id,
        )
        persist_reference_index(reference_id, output_path=args.index_output)
        receipt = _receipt(
            status="DONE",
            match_count_class=match_count_class,
            persisted=True,
            reason="OK",
        )
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0
    except BootstrapError as exc:
        receipt = _receipt(
            status="BLOCKED",
            match_count_class=_match_count_class_from_reason(exc.reason),
            persisted=False,
            reason=exc.reason,
        )
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 2


def parse_machine_access_token(access_token: str) -> tuple[str, str]:
    value = access_token.strip() if isinstance(access_token, str) else ""
    if not value.startswith("0.") or value.count(".") < 2:
        raise BootstrapError("MACHINE_TOKEN_SHAPE_INVALID")
    client_id, client_secret = value[2:].rsplit(".", 1)
    if not _TOKEN_PART_RE.fullmatch(client_id) or not _TOKEN_PART_RE.fullmatch(client_secret):
        raise BootstrapError("MACHINE_TOKEN_SHAPE_INVALID")
    return client_id, client_secret


def request_identity_token(
    *,
    identity_url: str,
    client_id: str,
    client_secret: str,
    timeout: int = 20,
) -> Mapping[str, Any]:
    if identity_url != IDENTITY_TOKEN_URL or urlparse(identity_url).scheme != "https":
        raise BootstrapError("IDENTITY_URL_NOT_ALLOWLISTED")
    body = urlencode(
        {
            "grant_type": "client_credentials",
            "scope": API_SCOPE,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("ascii")
    request = Request(
        identity_url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(131072)
    except OSError as exc:
        raise BootstrapError("IDENTITY_TOKEN_POST_FAILED") from exc
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("IDENTITY_TOKEN_RESPONSE_INVALID") from exc
    if not isinstance(decoded, Mapping):
        raise BootstrapError("IDENTITY_TOKEN_RESPONSE_INVALID")
    return decoded


def validate_identity_token_response(
    response: Mapping[str, Any],
    *,
    identity_url: str,
    client_id: str,
    now: int,
) -> str:
    token_type = response.get("token_type")
    if token_type != "Bearer":
        raise BootstrapError("IDENTITY_TOKEN_TYPE_INVALID")
    response_scope = _scope_set(response.get("scope"))
    if API_SCOPE not in response_scope:
        raise BootstrapError("IDENTITY_TOKEN_SCOPE_MISMATCH")
    access_token = response.get("access_token")
    if not isinstance(access_token, str):
        raise BootstrapError("IDENTITY_JWT_MALFORMED")
    payload = decode_jwt_payload(access_token)
    token_scope = _scope_set(payload.get("scope") or payload.get("scp"))
    if API_SCOPE not in token_scope:
        raise BootstrapError("IDENTITY_TOKEN_SCOPE_MISMATCH")
    expected_origin = _identity_origin(identity_url)
    issuer = payload.get("iss")
    if issuer not in {expected_origin, expected_origin + "/"}:
        raise BootstrapError("IDENTITY_TOKEN_ISSUER_MISMATCH")
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp <= now:
        raise BootstrapError("IDENTITY_TOKEN_EXPIRED")
    organization = payload.get("organization")
    if not isinstance(organization, str) or not _UUID_RE.fullmatch(organization):
        raise BootstrapError("IDENTITY_TOKEN_ORGANIZATION_INVALID")
    token_client = payload.get("client_id")
    if token_client is not None and token_client != client_id:
        raise BootstrapError("IDENTITY_TOKEN_CLIENT_ID_MISMATCH")
    return organization.lower()


def decode_jwt_payload(token: str) -> Mapping[str, Any]:
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise BootstrapError("IDENTITY_JWT_MALFORMED")
    try:
        payload_bytes = _b64url_decode(parts[1])
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("IDENTITY_JWT_MALFORMED") from exc
    if not isinstance(payload, Mapping):
        raise BootstrapError("IDENTITY_JWT_MALFORMED")
    return payload


def discover_gmail_secret_reference(
    *,
    machine_token: str,
    organization_id: str,
) -> tuple[str, str]:
    client = _bitwarden_client()
    _sdk_login_access_token(client, machine_token)
    secrets = client.secrets()
    rows = secrets.list(organization_id)
    matches = tuple(
        secret_id
        for secret_id, key in (_secret_identifier(item) for item in _iter_secret_rows(rows))
        if key == GMAIL_SECRET_KEY
    )
    unique = tuple(dict.fromkeys(matches))
    if len(unique) == 1 and len(matches) == 1:
        return unique[0], "one"
    if not unique:
        raise BootstrapError("SECRET_MATCH_NONE")
    raise BootstrapError("SECRET_MATCH_AMBIGUOUS")


def persist_reference_index(reference_id: str, *, output_path: str = REFERENCE_INDEX_OUTPUT) -> None:
    if not _UUID_RE.fullmatch(reference_id):
        raise BootstrapError("SECRET_REFERENCE_INVALID")
    index = {
        "schema": REFERENCE_INDEX_SCHEMA,
        "registrations": [
            {
                "service_id": SERVICE_ID,
                "alias": ALIAS,
                "provider": "bitwarden",
                "reference_id": reference_id.lower(),
            }
        ],
    }
    payload = json.dumps(index, sort_keys=True, separators=(",", ":"))
    command = [
        "systemd-creds",
        "encrypt",
        "--name",
        REFERENCE_INDEX_CREDENTIAL,
        "-",
        output_path,
    ]
    try:
        result = subprocess.run(
            command,
            input=payload,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BootstrapError("REFERENCE_INDEX_ENCRYPT_FAILED") from exc
    if result.returncode != 0:
        raise BootstrapError("REFERENCE_INDEX_ENCRYPT_FAILED")


def _bitwarden_client() -> Any:
    try:
        import bitwarden_sdk  # type: ignore[import-not-found]
    except ImportError as exc:
        raise BootstrapError("BITWARDEN_SDK_UNAVAILABLE") from exc
    factory = getattr(bitwarden_sdk, "BitwardenClient", None) or getattr(bitwarden_sdk, "Client", None)
    if factory is None:
        raise BootstrapError("BITWARDEN_SDK_CONTRACT_INVALID")
    try:
        return factory()
    except TypeError:
        return factory(None)


def _sdk_login_access_token(client: Any, machine_token: str) -> None:
    if hasattr(client, "login_access_token"):
        client.login_access_token(machine_token)
        return
    auth = getattr(client, "auth", None)
    if callable(auth):
        auth = auth()
    if auth is not None and hasattr(auth, "login_access_token"):
        auth.login_access_token(machine_token)
        return
    raise BootstrapError("BITWARDEN_SDK_CONTRACT_INVALID")


def _secret_identifier(item: Any) -> tuple[str, str]:
    if isinstance(item, Mapping):
        secret_id = item.get("id")
        key = item.get("key")
    else:
        secret_id = getattr(item, "id", None)
        key = getattr(item, "key", None)
    if not isinstance(secret_id, str) or not _UUID_RE.fullmatch(secret_id):
        raise BootstrapError("SECRET_METADATA_INVALID")
    if not isinstance(key, str):
        raise BootstrapError("SECRET_METADATA_INVALID")
    return secret_id.lower(), key


def _iter_secret_rows(rows: Any) -> tuple[Any, ...]:
    if isinstance(rows, Mapping):
        for field in ("data", "items", "secrets"):
            value = rows.get(field)
            if isinstance(value, list):
                return tuple(value)
        raise BootstrapError("SECRET_METADATA_INVALID")
    if isinstance(rows, list) or isinstance(rows, tuple):
        return tuple(rows)
    data = getattr(rows, "data", None) or getattr(rows, "items", None) or getattr(rows, "secrets", None)
    if isinstance(data, list) or isinstance(data, tuple):
        return tuple(data)
    raise BootstrapError("SECRET_METADATA_INVALID")


def _scope_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(value.split())
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    return set()


def _identity_origin(identity_url: str) -> str:
    parsed = urlparse(identity_url)
    if parsed.scheme != "https" or parsed.netloc != "identity.bitwarden.com" or parsed.path != "/connect/token":
        raise BootstrapError("IDENTITY_URL_NOT_ALLOWLISTED")
    return "https://identity.bitwarden.com"


def _credential_directory(raw: str) -> Path:
    path = Path(raw.strip() if isinstance(raw, str) else "")
    if not path.is_absolute() or not path.is_dir():
        raise BootstrapError("CREDENTIALS_DIRECTORY_UNAVAILABLE")
    return path


def _read_private_credential(directory: Path, name: str) -> str:
    if "/" in name or "\\" in name or name in {"", ".", ".."}:
        raise BootstrapError("CREDENTIAL_NAME_INVALID")
    path = directory / name
    if not path.is_file():
        raise BootstrapError("MACHINE_TOKEN_CREDENTIAL_UNAVAILABLE")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise BootstrapError("MACHINE_TOKEN_CREDENTIAL_READ_FAILED") from exc
    if not value:
        raise BootstrapError("MACHINE_TOKEN_CREDENTIAL_EMPTY")
    return value


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _receipt(
    *,
    status: str,
    match_count_class: str,
    persisted: bool,
    reason: str,
) -> dict[str, object]:
    return {
        "status": status,
        "match_count_class": match_count_class,
        "persisted": persisted,
        "reason": reason,
    }


def _match_count_class_from_reason(reason: str) -> str:
    if reason == "SECRET_MATCH_NONE":
        return "none"
    if reason == "SECRET_MATCH_AMBIGUOUS":
        return "ambiguous"
    return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
