#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
from collections.abc import Mapping
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


IDENTITY_TOKEN_URL = "https://identity.bitwarden.com/connect/token"
SECRETS_METADATA_URL = "https://api.bitwarden.com/api/secrets"
SCHEMA = "skeleton.secret_reference_index.v1"
ACCESS_TOKEN_CREDENTIAL = "bitwarden-access-token"
REFERENCE_INDEX_CREDENTIAL = "skeleton-secret-reference-index"
SERVICE_ID = "mail-gmail"
ALIAS = "acct:gmail-primary"
GMAIL_PRIMARY_KEY = "gmail-primary-oauth"
PINNED_BITWARDEN_SDK_VERSION = "2.1.0"
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class HelperError(RuntimeError):
    pass


def _verify_pinned_sdk_runtime() -> None:
    try:
        version = importlib.metadata.version("bitwarden-sdk")
    except importlib.metadata.PackageNotFoundError as exc:
        raise HelperError("bitwarden_sdk_runtime_unavailable") from exc
    if version != PINNED_BITWARDEN_SDK_VERSION:
        raise HelperError("bitwarden_sdk_runtime_version_mismatch")


def _read_credential(name: str, environment: Mapping[str, str] = os.environ) -> str:
    directory = environment.get("CREDENTIALS_DIRECTORY", "").strip()
    if not directory or not Path(directory).is_absolute():
        raise HelperError("systemd_credentials_directory_unavailable")
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise HelperError("invalid_systemd_credential_name")
    path = Path(directory) / name
    if not path.is_file():
        raise HelperError("systemd_credential_unavailable")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise HelperError("systemd_credential_read_failed") from exc
    if not value:
        raise HelperError("systemd_credential_empty")
    return value


def _parse_machine_token(token: str) -> tuple[str, str]:
    parts = token.strip().split(".", 2)
    if len(parts) != 3 or parts[0] != "0" or not UUID_RE.fullmatch(parts[1]):
        raise HelperError("bitwarden_access_token_contract_mismatch")
    secret = parts[2].split(":", 1)[0].rsplit(".", 1)[0]
    if not secret:
        raise HelperError("bitwarden_access_token_contract_mismatch")
    return parts[1], secret


def _post_identity_token(client_id: str, client_secret: str) -> str:
    encoded = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "scope": "api.secrets",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        IDENTITY_TOKEN_URL,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(1024 * 1024)
    except (OSError, urllib.error.URLError) as exc:
        raise HelperError("bitwarden_identity_exchange_failed") from exc
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HelperError("bitwarden_identity_response_invalid") from exc
    access_token = payload.get("access_token") if isinstance(payload, Mapping) else None
    if not isinstance(access_token, str) or not access_token:
        raise HelperError("bitwarden_identity_access_token_missing")
    return access_token


def _jwt_claims(jwt: str) -> Mapping[str, object]:
    parts = jwt.split(".")
    if len(parts) < 2:
        raise HelperError("bitwarden_identity_jwt_invalid")
    segment = parts[1]
    segment += "=" * ((4 - len(segment) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(segment.encode("ascii")).decode("utf-8")
        claims = json.loads(decoded)
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise HelperError("bitwarden_identity_jwt_invalid") from exc
    if not isinstance(claims, Mapping):
        raise HelperError("bitwarden_identity_jwt_invalid")
    return claims


def _organization_id_from_claims(claims: Mapping[str, object]) -> str:
    candidates = {
        value
        for key in ("organizationId", "organization_id", "org_id", "org")
        for value in (claims.get(key),)
        if isinstance(value, str) and UUID_RE.fullmatch(value)
    }
    organization = claims.get("organization")
    if isinstance(organization, Mapping):
        value = organization.get("id")
        if isinstance(value, str) and UUID_RE.fullmatch(value):
            candidates.add(value)
    if len(candidates) != 1:
        raise HelperError("bitwarden_organization_claim_unavailable")
    return next(iter(candidates)).lower()


def organization_id_from_machine_token(token: str) -> str:
    client_id, client_secret = _parse_machine_token(token)
    return _organization_id_from_claims(_jwt_claims(_post_identity_token(client_id, client_secret)))


def _list_secret_metadata(bearer_token: str, organization_id: str) -> list[Mapping[str, object]]:
    query = urllib.parse.urlencode({"organizationId": organization_id})
    request = urllib.request.Request(
        f"{SECRETS_METADATA_URL}?{query}",
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(1024 * 1024)
    except (OSError, urllib.error.URLError) as exc:
        raise HelperError("bitwarden_metadata_list_failed") from exc
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HelperError("bitwarden_metadata_response_invalid") from exc
    if isinstance(decoded, Mapping):
        items = decoded.get("data") or decoded.get("secrets") or decoded.get("items")
    else:
        items = decoded
    if not isinstance(items, list):
        raise HelperError("bitwarden_metadata_response_invalid")
    if len(items) > 512:
        raise HelperError("bitwarden_metadata_response_too_large")
    return [item for item in items if isinstance(item, Mapping)]


def _match_gmail_primary_reference(items: list[Mapping[str, object]], organization_id: str) -> str:
    matches: list[str] = []
    for item in items:
        if "value" in item or "note" in item:
            raise HelperError("bitwarden_metadata_contains_value_fields")
        if item.get("organizationId") != organization_id:
            continue
        if item.get("key") not in {GMAIL_PRIMARY_KEY, ALIAS}:
            continue
        reference_id = item.get("id")
        if not isinstance(reference_id, str) or not UUID_RE.fullmatch(reference_id):
            raise HelperError("bitwarden_metadata_reference_invalid")
        matches.append(reference_id.lower())
    if not matches:
        raise HelperError("bitwarden_gmail_reference_zero_matches")
    if len(matches) != 1 or len(set(matches)) != 1:
        raise HelperError("bitwarden_gmail_reference_many_matches")
    return matches[0]


def build_gmail_primary_reference_index(machine_token: str) -> dict[str, object]:
    client_id, client_secret = _parse_machine_token(machine_token)
    bearer_token = _post_identity_token(client_id, client_secret)
    organization_id = _organization_id_from_claims(_jwt_claims(bearer_token))
    reference_id = _match_gmail_primary_reference(
        _list_secret_metadata(bearer_token, organization_id),
        organization_id,
    )
    return {
        "schema": SCHEMA,
        "registrations": [
            {
                "service_id": SERVICE_ID,
                "alias": ALIAS,
                "provider": "bitwarden",
                "reference_id": reference_id,
            }
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("bootstrap-gmail-primary-index",))
    args = parser.parse_args(argv)
    del args
    try:
        _verify_pinned_sdk_runtime()
        payload = build_gmail_primary_reference_index(_read_credential(ACCESS_TOKEN_CREDENTIAL))
    except HelperError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"status": "DONE", "index": payload}, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
