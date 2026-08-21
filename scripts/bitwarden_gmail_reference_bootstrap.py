#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request


IDENTITY_TOKEN_URL = "https://identity.bitwarden.com/connect/token"
SDK_PACKAGE_VERSION = "2.1.0"
SERVICE_ID = "mail-gmail"
ACCOUNT_ALIAS = "acct:gmail-primary"
CANONICAL_GMAIL_SECRET_KEY = "GMAIL_PRIMARY_OAUTH_BUNDLE"
REFERENCE_INDEX_CREDENTIAL_NAME = "skeleton-secret-reference-index"
REFERENCE_INDEX_CREDENTIAL_PATH = (
    "/etc/credstore.encrypted/"
    "skeleton-mail-operations.service/"
    "skeleton-secret-reference-index"
)
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
MACHINE_TOKEN_RE = re.compile(
    r"^0\.(?P<client_id>[A-Za-z0-9_-]{8,})\.(?P<client_secret>[A-Za-z0-9_-]{16,}):(?P<encryption_key>[A-Za-z0-9_+/=-]{16,})$"
)


class BootstrapError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MachineTokenParts:
    client_id: str
    client_secret: str
    encryption_key: str


def parse_machine_token(machine_token: str) -> MachineTokenParts:
    if machine_token != machine_token.strip() or any(ch.isspace() for ch in machine_token):
        raise BootstrapError("machine_token_invalid")
    match = MACHINE_TOKEN_RE.fullmatch(machine_token)
    if match is None:
        raise BootstrapError("machine_token_invalid")
    return MachineTokenParts(
        client_id=match.group("client_id"),
        client_secret=match.group("client_secret"),
        encryption_key=match.group("encryption_key"),
    )


def request_identity_jwt(
    machine_token: str,
    *,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> str:
    parts = parse_machine_token(machine_token)
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "scope": "api.organization",
            "client_id": parts.client_id,
            "client_secret": parts.client_secret,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        IDENTITY_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        response = opener(request, timeout=30)
        raw = response.read()
    except Exception as exc:  # pragma: no cover - exact transport type varies.
        raise BootstrapError("identity_auth_failed") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("identity_auth_invalid") from exc
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or access_token.count(".") != 2:
        raise BootstrapError("identity_auth_invalid")
    return access_token


def organization_id_from_jwt(access_token: str) -> str:
    try:
        payload_segment = access_token.split(".")[1]
        padded = payload_segment + "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception as exc:
        raise BootstrapError("identity_jwt_invalid") from exc
    organization_id = (
        payload.get("organization")
        or payload.get("organization_id")
        or payload.get("org")
    )
    if not isinstance(organization_id, str) or not UUID_RE.fullmatch(organization_id):
        raise BootstrapError("identity_organization_claim_missing")
    return organization_id.lower()


def _sdk_client() -> object:
    try:
        import bitwarden_sdk  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - exercised by installer smoke.
        raise BootstrapError("bitwarden_sdk_unavailable") from exc
    for name in ("BitwardenClient", "Client"):
        client_type = getattr(bitwarden_sdk, name, None)
        if client_type is not None:
            return client_type()
    raise BootstrapError("bitwarden_sdk_client_unavailable")


def _sdk_secret_items(client: object, organization_id: str) -> Sequence[object]:
    secrets = getattr(client, "secrets")()
    result = secrets.list(organization_id)
    data = getattr(result, "data", result)
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        return data
    raise BootstrapError("bitwarden_metadata_invalid")


def _item_key(item: object) -> str | None:
    if isinstance(item, Mapping):
        value = item.get("key")
    else:
        value = getattr(item, "key", None)
    return value if isinstance(value, str) else None


def _item_id(item: object) -> str | None:
    if isinstance(item, Mapping):
        value = item.get("id")
    else:
        value = getattr(item, "id", None)
    return value.lower() if isinstance(value, str) and UUID_RE.fullmatch(value) else None


def discover_canonical_gmail_reference(
    machine_token: str,
    *,
    client: object | None = None,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> str:
    jwt = request_identity_jwt(machine_token, opener=opener)
    organization_id = organization_id_from_jwt(jwt)
    sdk_client = client if client is not None else _sdk_client()
    login = getattr(sdk_client, "login_access_token", None)
    if not callable(login):
        raise BootstrapError("bitwarden_sdk_login_unavailable")
    login(machine_token)
    matches = [
        reference_id
        for item in _sdk_secret_items(sdk_client, organization_id)
        if _item_key(item) == CANONICAL_GMAIL_SECRET_KEY
        for reference_id in [_item_id(item)]
        if reference_id is not None
    ]
    if not matches:
        raise BootstrapError("gmail_reference_not_found")
    if len(set(matches)) != 1 or len(matches) != 1:
        raise BootstrapError("gmail_reference_ambiguous")
    return matches[0]


def reference_index_payload(reference_id: str) -> str:
    if not UUID_RE.fullmatch(reference_id):
        raise BootstrapError("reference_id_invalid")
    return json.dumps(
        {
            "schema": "skeleton.secret_reference_index.v1",
            "registrations": [
                {
                    "service_id": SERVICE_ID,
                    "alias": ACCOUNT_ALIAS,
                    "provider": "bitwarden",
                    "reference_id": reference_id.lower(),
                }
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def encrypt_reference_index(
    reference_id: str,
    *,
    output_path: str = REFERENCE_INDEX_CREDENTIAL_PATH,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    payload = reference_index_payload(reference_id)
    result = runner(
        [
            "systemd-creds",
            "encrypt",
            "--name",
            REFERENCE_INDEX_CREDENTIAL_NAME,
            "-",
            output_path,
        ],
        input=payload,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise BootstrapError("systemd_creds_encrypt_failed")


def bootstrap(machine_token: str) -> dict[str, object]:
    reference_id = discover_canonical_gmail_reference(machine_token)
    encrypt_reference_index(reference_id)
    return {
        "schema": "skeleton.mail_gmail_bitwarden_reference_bootstrap.receipt.v1",
        "status": "DONE",
        "service_id": SERVICE_ID,
        "alias": ACCOUNT_ALIAS,
        "provider": "bitwarden",
        "reference_registration": "encrypted",
        "secret_value_read": False,
        "plaintext_index_file": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine-token-file", required=True)
    args = parser.parse_args(argv)
    try:
        with open(args.machine_token_file, "r", encoding="utf-8") as handle:
            machine_token = handle.read().strip()
        receipt = bootstrap(machine_token)
    except BootstrapError as exc:
        print(
            json.dumps(
                {
                    "schema": "skeleton.mail_gmail_bitwarden_reference_bootstrap.receipt.v1",
                    "status": "BLOCKED",
                    "reason": str(exc),
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
