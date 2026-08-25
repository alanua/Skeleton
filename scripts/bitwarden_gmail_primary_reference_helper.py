#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.secret_reference import REFERENCE_INDEX_CREDENTIAL_NAME
from integrations.bitwarden_secret_store import (
    BITWARDEN_ACCESS_TOKEN_CREDENTIAL,
    BitwardenMachineTokenError,
    bitwarden_identity_oauth_form,
    parse_bitwarden_machine_token,
)


BITWARDEN_ORGANIZATION_ID_CREDENTIAL = "bitwarden-organization-id"
GMAIL_PRIMARY_SECRET_KEY = "gmail-primary-oauth-bundle"
GMAIL_PRIMARY_ALIAS = "acct:gmail-primary"
GMAIL_SERVICE_ID = "mail-gmail"
ENCRYPTED_CREDENTIAL_ROOT = Path("/etc/credstore.encrypted")


class HelperError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SecretMetadata:
    id: str
    key: str


def _credential_path(authority_environment: Mapping[str, str], name: str) -> Path:
    directory = authority_environment.get("CREDENTIALS_DIRECTORY", "").strip()
    if not directory or not Path(directory).is_absolute():
        raise HelperError("systemd_credentials_directory_unavailable")
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise HelperError("invalid_systemd_credential_name")
    path = Path(directory) / name
    if not path.is_file():
        raise HelperError("systemd_credential_unavailable")
    return path


def _read_credential(authority_environment: Mapping[str, str], name: str) -> str:
    try:
        value = _credential_path(authority_environment, name).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise HelperError("systemd_credential_read_failed") from exc
    if not value:
        raise HelperError("systemd_credential_empty")
    return value


def _metadata_from_item(item: object) -> SecretMetadata:
    data = getattr(item, "data", item)
    secret_id = getattr(data, "id", None)
    key = getattr(data, "key", None)
    if isinstance(data, Mapping):
        secret_id = data.get("id")
        key = data.get("key")
    if not isinstance(secret_id, str) or not isinstance(key, str):
        raise HelperError("bitwarden_secret_metadata_contract_mismatch")
    return SecretMetadata(id=secret_id, key=key)


def _list_data(result: object) -> Sequence[object]:
    data = getattr(result, "data", result)
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        return data
    raise HelperError("bitwarden_secret_list_contract_mismatch")


def discover_gmail_primary_reference_id(client: object, organization_id: str) -> str:
    secrets = getattr(client, "secrets", None)
    if not callable(secrets):
        raise HelperError("bitwarden_sdk_secrets_unavailable")
    secret_client = secrets()
    list_method = getattr(secret_client, "list", None)
    if not callable(list_method):
        raise HelperError("bitwarden_sdk_secret_list_unavailable")
    matches = [
        metadata.id
        for metadata in (_metadata_from_item(item) for item in _list_data(list_method(organization_id)))
        if metadata.key == GMAIL_PRIMARY_SECRET_KEY
    ]
    if not matches:
        raise HelperError("gmail_primary_reference_not_found")
    if len(set(matches)) != 1 or len(matches) != 1:
        raise HelperError("gmail_primary_reference_ambiguous")
    return matches[0]


def reference_index_payload(reference_id: str) -> bytes:
    payload = {
        "schema": "skeleton.secret_reference_index.v1",
        "registrations": [
            {
                "service_id": GMAIL_SERVICE_ID,
                "alias": GMAIL_PRIMARY_ALIAS,
                "provider": "bitwarden",
                "reference_id": reference_id,
            }
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def persist_reference_index_with_systemd_creds(
    payload: bytes,
    *,
    encrypted_credential_root: Path = ENCRYPTED_CREDENTIAL_ROOT,
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    if not payload:
        raise HelperError("reference_index_payload_empty")
    destination = encrypted_credential_root / REFERENCE_INDEX_CREDENTIAL_NAME
    command = [
        "systemd-creds",
        "encrypt",
        f"--name={REFERENCE_INDEX_CREDENTIAL_NAME}",
        "-",
        str(destination),
    ]
    try:
        result = run(
            command,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HelperError("systemd_creds_encrypt_execution_failed") from exc
    if result.returncode != 0:
        raise HelperError("systemd_creds_encrypt_failed")


def _build_sdk_client() -> object:
    try:
        from bitwarden_sdk import BitwardenClient
    except ImportError as exc:
        raise HelperError("bitwarden_sdk_unavailable") from exc
    return BitwardenClient()


def login_sdk_with_original_machine_token(client: object, machine_token: str) -> None:
    auth = getattr(client, "auth", None)
    if not callable(auth):
        raise HelperError("bitwarden_sdk_auth_unavailable")
    login = getattr(auth(), "login_access_token", None)
    if not callable(login):
        raise HelperError("bitwarden_sdk_login_unavailable")
    login(machine_token)


def bootstrap_gmail_primary_reference_index(
    authority_environment: Mapping[str, str],
    *,
    client_factory: Callable[[], object] = _build_sdk_client,
    encrypted_credential_root: Path = ENCRYPTED_CREDENTIAL_ROOT,
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    machine_token = _read_credential(authority_environment, BITWARDEN_ACCESS_TOKEN_CREDENTIAL)
    organization_id = _read_credential(authority_environment, BITWARDEN_ORGANIZATION_ID_CREDENTIAL)
    try:
        parsed_token = parse_bitwarden_machine_token(machine_token)
    except BitwardenMachineTokenError as exc:
        raise HelperError("bitwarden_machine_token_invalid") from exc
    bitwarden_identity_oauth_form(parsed_token)
    client = client_factory()
    login_sdk_with_original_machine_token(client, parsed_token.original)
    reference_id = discover_gmail_primary_reference_id(client, organization_id)
    persist_reference_index_with_systemd_creds(
        reference_index_payload(reference_id),
        encrypted_credential_root=encrypted_credential_root,
        run=run,
    )


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Gmail primary Bitwarden reference metadata.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    sub.add_parser("bootstrap")
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            _build_sdk_client()
            print("bitwarden_sdk_runtime=ready")
            return 0
        bootstrap_gmail_primary_reference_index(os.environ)
        print("gmail_primary_reference_index=encrypted")
        return 0
    except HelperError as exc:
        print(f"reason={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
