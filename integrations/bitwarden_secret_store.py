from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import base64
import json
from pathlib import Path
import shutil
import subprocess
import urllib.parse
import urllib.request
from uuid import UUID

from core.secret_store import (
    ResolvedSecret,
    SecretMissing,
    SecretProviderUnavailable,
    SecretReference,
    SecretResolutionContext,
    SecretStore,
)


BITWARDEN_ACCESS_TOKEN_CREDENTIAL = "bitwarden-access-token"
BITWARDEN_IDENTITY_URL = "https://identity.bitwarden.com"
BITWARDEN_API_URL = "https://api.bitwarden.com"
BITWARDEN_SDK_VERSION = "2.1.0"
GMAIL_PRIMARY_REFERENCE_KEYS = frozenset(
    {
        "gmail-primary-oauth-secret-ref",
        "gmail-primary-oauth",
        "acct:gmail-primary",
    }
)


class BitwardenReferenceDiscoveryError(ValueError):
    pass


def _credential_path(authority_environment: Mapping[str, str], name: str) -> Path:
    directory = authority_environment.get("CREDENTIALS_DIRECTORY", "").strip()
    if not directory or not Path(directory).is_absolute():
        raise SecretProviderUnavailable("systemd_credentials_directory_unavailable")
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise SecretProviderUnavailable("invalid_systemd_credential_name")
    path = Path(directory) / name
    if not path.is_file():
        raise SecretProviderUnavailable("systemd_credential_unavailable")
    return path


def _read_credential(authority_environment: Mapping[str, str], name: str) -> str:
    try:
        value = _credential_path(authority_environment, name).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise SecretProviderUnavailable("systemd_credential_read_failed") from exc
    if not value:
        raise SecretProviderUnavailable("systemd_credential_empty")
    return value


def bitwarden_machine_token_from_systemd(
    authority_environment: Mapping[str, str],
    *,
    token_credential_name: str = BITWARDEN_ACCESS_TOKEN_CREDENTIAL,
) -> str:
    return _read_credential(authority_environment, token_credential_name)


def bitwarden_reference_from_systemd_credential(
    authority_environment: Mapping[str, str],
    credential_name: str,
) -> SecretReference:
    return SecretReference(
        provider="bitwarden",
        reference_id=_read_credential(authority_environment, credential_name),
    )


def derive_bitwarden_organization_id_from_machine_token(
    access_token: str,
    *,
    identity_url: str = BITWARDEN_IDENTITY_URL,
    timeout_seconds: int = 20,
) -> str:
    """Exchange an existing machine token at the fixed identity endpoint and derive org id."""

    client_id, client_secret = _machine_token_client_parts(access_token)
    endpoint = _fixed_identity_token_endpoint(identity_url)
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "scope": "api.secrets",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
        raise BitwardenReferenceDiscoveryError("identity_exchange_failed") from exc
    if not isinstance(payload, Mapping):
        raise BitwardenReferenceDiscoveryError("identity_exchange_contract_mismatch")
    bearer = payload.get("access_token")
    if not isinstance(bearer, str) or not bearer:
        raise BitwardenReferenceDiscoveryError("identity_exchange_missing_access_token")
    return _organization_id_from_identity_jwt(bearer)


def discover_gmail_primary_reference_with_sdk(
    *,
    sdk_python: str,
    access_token: str,
    organization_id: str,
    api_url: str = BITWARDEN_API_URL,
    identity_url: str = BITWARDEN_IDENTITY_URL,
    timeout_seconds: int = 45,
) -> str:
    """Return one opaque UUID discovered via the official SDK identifier list surface."""

    _validate_uuid(organization_id, "organization_id_invalid")
    script = r'''
from __future__ import annotations

import importlib.metadata
import json
import os
import sys
from uuid import UUID

EXPECTED_VERSION = "2.1.0"
MATCH_KEYS = {"gmail-primary-oauth-secret-ref", "gmail-primary-oauth", "acct:gmail-primary"}


def fail(reason: str) -> None:
    print(json.dumps({"status": "blocked", "reason": reason}), file=sys.stderr)
    raise SystemExit(1)


def as_items(response):
    data = getattr(response, "data", response)
    for attr in ("data", "secrets", "items", "secret_identifiers"):
        value = getattr(data, attr, None)
        if isinstance(value, list):
            return value
    if isinstance(data, list):
        return data
    if isinstance(response, dict):
        for key in ("data", "secrets", "items", "secret_identifiers"):
            value = response.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = value.get("data")
                if isinstance(nested, list):
                    return nested
    fail("sdk_identifier_contract_mismatch")


def field(item, name: str):
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


if importlib.metadata.version("bitwarden-sdk") != EXPECTED_VERSION:
    fail("sdk_version_mismatch")

from bitwarden_sdk import BitwardenClient, DeviceType, client_settings_from_dict

organization_id = os.environ["BW_ORGANIZATION_ID"]
UUID(organization_id)
client = BitwardenClient(
    client_settings_from_dict(
        {
            "apiUrl": os.environ["BW_API_URL"],
            "identityUrl": os.environ["BW_IDENTITY_URL"],
            "deviceType": DeviceType.SDK,
            "userAgent": "SkeletonMailReferenceDiscovery/1",
        }
    )
)
client.auth().login_access_token(os.environ["BWS_ACCESS_TOKEN"], None)
secrets_client = client.secrets()
secret_identifiers = secrets_client.list(organization_id)
matches = []
for item in as_items(secret_identifiers):
    secret_id = field(item, "id")
    key = field(item, "key")
    item_org = field(item, "organization_id") or field(item, "organizationId")
    if not isinstance(secret_id, str) or not isinstance(key, str):
        fail("sdk_identifier_contract_mismatch")
    UUID(secret_id)
    if item_org is not None and str(item_org) != organization_id:
        continue
    if key in MATCH_KEYS:
        matches.append(secret_id)
print(json.dumps({"matches": matches}, separators=(",", ":")))
'''
    env = {
        "BWS_ACCESS_TOKEN": access_token,
        "BW_ORGANIZATION_ID": organization_id,
        "BW_API_URL": api_url,
        "BW_IDENTITY_URL": identity_url,
    }
    try:
        result = subprocess.run(
            [sdk_python, "-I", "-c", script],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BitwardenReferenceDiscoveryError("sdk_identifier_list_failed") from exc
    if result.returncode != 0:
        raise BitwardenReferenceDiscoveryError("sdk_identifier_list_failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BitwardenReferenceDiscoveryError("sdk_identifier_contract_mismatch") from exc
    if not isinstance(payload, Mapping):
        raise BitwardenReferenceDiscoveryError("sdk_identifier_contract_mismatch")
    matches = payload.get("matches")
    if not isinstance(matches, list):
        raise BitwardenReferenceDiscoveryError("sdk_identifier_contract_mismatch")
    unique = []
    for item in matches:
        if not isinstance(item, str):
            raise BitwardenReferenceDiscoveryError("sdk_identifier_contract_mismatch")
        _validate_uuid(item, "sdk_identifier_contract_mismatch")
        if item not in unique:
            unique.append(item)
    if not unique:
        raise BitwardenReferenceDiscoveryError("gmail_primary_reference_zero_matches")
    if len(unique) != 1:
        raise BitwardenReferenceDiscoveryError("gmail_primary_reference_many_matches")
    return unique[0]


def discover_gmail_primary_reference(
    *,
    authority_environment: Mapping[str, str],
    sdk_python: str,
    identity_url: str = BITWARDEN_IDENTITY_URL,
    api_url: str = BITWARDEN_API_URL,
) -> str:
    token = bitwarden_machine_token_from_systemd(authority_environment)
    organization_id = derive_bitwarden_organization_id_from_machine_token(
        token,
        identity_url=identity_url,
    )
    return discover_gmail_primary_reference_with_sdk(
        sdk_python=sdk_python,
        access_token=token,
        organization_id=organization_id,
        api_url=api_url,
        identity_url=identity_url,
    )


def public_reference_discovery_receipt(
    *,
    status: str,
    reason: str,
    match_count: int,
) -> dict[str, object]:
    return {
        "status": status,
        "reason": reason,
        "zero_matches": match_count == 0,
        "one_match": match_count == 1,
        "many_matches": match_count > 1,
        "secret_values_exposed": False,
        "credential_directory_written": False,
    }


def _machine_token_client_parts(access_token: str) -> tuple[str, str]:
    token = access_token.strip() if isinstance(access_token, str) else ""
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "0" or not parts[1] or not parts[2]:
        raise BitwardenReferenceDiscoveryError("machine_token_contract_mismatch")
    return parts[1], parts[2]


def _fixed_identity_token_endpoint(identity_url: str) -> str:
    parsed = urllib.parse.urlparse(identity_url)
    if parsed.scheme != "https" or parsed.params or parsed.query or parsed.fragment:
        raise BitwardenReferenceDiscoveryError("identity_endpoint_untrusted")
    if parsed.path not in {"", "/"}:
        raise BitwardenReferenceDiscoveryError("identity_endpoint_untrusted")
    if parsed.netloc not in {"identity.bitwarden.com", "identity.bitwarden.eu"}:
        raise BitwardenReferenceDiscoveryError("identity_endpoint_untrusted")
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, "/connect/token", "", "", "")
    )


def _organization_id_from_identity_jwt(access_token: str) -> str:
    parts = access_token.split(".")
    if len(parts) < 2:
        raise BitwardenReferenceDiscoveryError("identity_token_contract_mismatch")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise BitwardenReferenceDiscoveryError("identity_token_contract_mismatch") from exc
    if not isinstance(claims, Mapping):
        raise BitwardenReferenceDiscoveryError("identity_token_contract_mismatch")
    candidates = [
        claims.get("accesssecretsmanager"),
        claims.get("organizationId"),
        claims.get("organization_id"),
    ]
    values = {
        str(value)
        for value in candidates
        if isinstance(value, str) and _is_uuid(value)
    }
    if len(values) != 1:
        raise BitwardenReferenceDiscoveryError("identity_organization_id_ambiguous")
    return next(iter(values))


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (TypeError, ValueError):
        return False
    return True


def _validate_uuid(value: str, reason: str) -> None:
    if not _is_uuid(value):
        raise BitwardenReferenceDiscoveryError(reason)


@dataclass(slots=True)
class BwsCliSecretsManagerStore(SecretStore):
    bws_path: str
    access_token: str
    home: str
    provider: str = "bitwarden"

    @classmethod
    def from_systemd_credentials(
        cls,
        authority_environment: Mapping[str, str],
        *,
        token_credential_name: str = BITWARDEN_ACCESS_TOKEN_CREDENTIAL,
    ) -> "BwsCliSecretsManagerStore":
        trusted_path = authority_environment.get("PATH", "")
        bws = shutil.which("bws", path=trusted_path)
        if not bws:
            raise SecretProviderUnavailable("bitwarden_bws_cli_unavailable")
        home = authority_environment.get("HOME", "").strip()
        if not home or not Path(home).is_absolute():
            raise SecretProviderUnavailable("bitwarden_trusted_home_unavailable")
        token = _read_credential(authority_environment, token_credential_name)
        return cls(bws_path=str(Path(bws).resolve(strict=False)), access_token=token, home=home)

    def __repr__(self) -> str:
        return f"BwsCliSecretsManagerStore(bws_path={self.bws_path!r}, access_token=<redacted>, home={self.home!r})"

    def resolve(self, reference: SecretReference, context: SecretResolutionContext) -> ResolvedSecret:
        del context  # Scope is enforced by SecretStoreGate before provider access.
        if reference.provider != self.provider:
            raise SecretProviderUnavailable("bitwarden_reference_provider_mismatch")
        if reference.version is not None:
            raise SecretProviderUnavailable("bitwarden_version_pin_unsupported")
        child_env = {
            "BWS_ACCESS_TOKEN": self.access_token,
            "HOME": self.home,
            "PATH": str(Path(self.bws_path).parent),
        }
        try:
            result = subprocess.run(
                [self.bws_path, "secret", "get", reference.reference_id, "--output", "json"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SecretProviderUnavailable("bitwarden_cli_execution_failed") from exc
        if result.returncode != 0:
            raise SecretProviderUnavailable("bitwarden_secret_get_failed")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise SecretProviderUnavailable("bitwarden_payload_invalid") from exc
        if not isinstance(payload, dict):
            raise SecretProviderUnavailable("bitwarden_payload_contract_mismatch")
        if payload.get("id") != reference.reference_id:
            raise SecretProviderUnavailable("bitwarden_reference_mismatch")
        value = payload.get("value")
        if not isinstance(value, str) or not value:
            raise SecretMissing("bitwarden_secret_missing")
        return ResolvedSecret(value)
