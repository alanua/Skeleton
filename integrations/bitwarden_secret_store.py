from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from core.secret_store import (
    ResolvedSecret,
    SecretMissing,
    SecretProviderUnavailable,
    SecretReference,
    SecretResolutionContext,
    SecretStore,
)


BITWARDEN_ACCESS_TOKEN_CREDENTIAL = "bitwarden-access-token"
BITWARDEN_ORGANIZATION_ID_CREDENTIAL = "bitwarden-organization-id"
BITWARDEN_SDK_RUNTIME_ROOT = Path("/opt/skeleton-bitwarden-sdk")
BITWARDEN_SDK_PYTHON = BITWARDEN_SDK_RUNTIME_ROOT / "bin" / "python"
BITWARDEN_IDENTIFIER_HELPER = BITWARDEN_SDK_RUNTIME_ROOT / "bitwarden_secret_identifier_helper.py"
BITWARDEN_IDENTIFIER_HELPER_TIMEOUT_SECONDS = 45
BITWARDEN_REFERENCE_INDEX_ENCRYPTED_PATH = Path(
    "/etc/credstore.encrypted/skeleton-secret-reference-index"
)

_REGISTERED_IDENTIFIER_MATCHERS = {
    ("mail-gmail", "acct:gmail-primary"): (
        "gmail-primary-oauth",
        "skeleton/mail/gmail-primary/oauth",
        "mail-gmail/acct:gmail-primary/oauth",
    ),
}


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


def bitwarden_reference_from_systemd_credential(
    authority_environment: Mapping[str, str],
    credential_name: str,
) -> SecretReference:
    return SecretReference(provider="bitwarden", reference_id=_read_credential(authority_environment, credential_name))


def bootstrap_registered_bitwarden_reference_index(
    authority_environment: Mapping[str, str],
    *,
    service_id: str,
    alias: str,
) -> dict[str, object]:
    """Discover one registered Bitwarden identifier and store only its opaque id.

    The long-running Runner Python never imports bitwarden_sdk. Discovery is
    delegated to a code-owned helper installed inside the fixed isolated SDK
    runtime. The helper receives no caller-selected query string and must not
    emit secret values.
    """

    expected_keys = _REGISTERED_IDENTIFIER_MATCHERS.get((service_id, alias))
    if expected_keys is None:
        return {"status": "BLOCKED", "reason": "REGISTERED_IDENTIFIER_MATCHER_UNAVAILABLE"}
    token = _read_credential(authority_environment, BITWARDEN_ACCESS_TOKEN_CREDENTIAL)
    organization_id = _read_credential(authority_environment, BITWARDEN_ORGANIZATION_ID_CREDENTIAL)
    index_path = _credential_path(authority_environment, "skeleton-secret-reference-index")
    child_env = {
        "BWS_ACCESS_TOKEN": token,
        "BWS_ORGANIZATION_ID": organization_id,
        "SKELETON_BITWARDEN_SERVICE_ID": service_id,
        "SKELETON_BITWARDEN_ALIAS": alias,
        "SKELETON_BITWARDEN_EXPECTED_KEYS_JSON": json.dumps(list(expected_keys)),
    }
    for optional_name in ("BW_API_URL", "BW_IDENTITY_URL"):
        value = authority_environment.get(optional_name, "").strip()
        if value:
            child_env[optional_name] = value
    try:
        result = subprocess.run(
            [str(BITWARDEN_SDK_PYTHON), str(BITWARDEN_IDENTIFIER_HELPER)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
            timeout=BITWARDEN_IDENTIFIER_HELPER_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"status": "BLOCKED", "reason": "IDENTIFIER_HELPER_EXECUTION_FAILED"}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        if result.returncode != 0:
            return {"status": "BLOCKED", "reason": "IDENTIFIER_HELPER_FAILED"}
        return {"status": "BLOCKED", "reason": "IDENTIFIER_HELPER_INVALID_OUTPUT"}
    if not isinstance(payload, dict):
        return {"status": "BLOCKED", "reason": "IDENTIFIER_HELPER_INVALID_OUTPUT"}
    if result.returncode != 0:
        reason = payload.get("reason")
        if not isinstance(reason, str):
            reason = "IDENTIFIER_HELPER_FAILED"
        return {"status": "BLOCKED", "reason": reason}
    match_status = payload.get("match_status")
    if match_status == "ZERO":
        return {"status": "BLOCKED", "reason": "IDENTIFIER_MATCH_ZERO", "match_status": "ZERO"}
    if match_status == "MANY":
        return {"status": "BLOCKED", "reason": "IDENTIFIER_MATCH_AMBIGUOUS", "match_status": "MANY"}
    if match_status != "ONE":
        return {"status": "BLOCKED", "reason": "IDENTIFIER_HELPER_INVALID_OUTPUT"}
    reference_id = payload.get("reference_id")
    matched_key = payload.get("matched_key")
    if not isinstance(reference_id, str) or not isinstance(matched_key, str):
        return {"status": "BLOCKED", "reason": "IDENTIFIER_HELPER_INVALID_OUTPUT"}
    if matched_key not in expected_keys:
        return {"status": "BLOCKED", "reason": "IDENTIFIER_MATCHER_CONTRACT_MISMATCH"}
    serialized_index = _registered_reference_index_payload(
        index_path,
        service_id=service_id,
        alias=alias,
        reference_id=reference_id,
    )
    _encrypt_registered_reference_index(serialized_index)
    _write_registered_reference_index(index_path, serialized_index)
    return {"status": "DONE", "reason": "OK", "match_status": "ONE"}


def _registered_reference_index_payload(
    path: Path,
    *,
    service_id: str,
    alias: str,
    reference_id: str,
) -> str:
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SecretProviderUnavailable("reference_index_invalid") from exc
    except (OSError, UnicodeError) as exc:
        raise SecretProviderUnavailable("reference_index_read_failed") from exc
    if not isinstance(current, dict):
        raise SecretProviderUnavailable("reference_index_invalid")
    registrations = current.get("registrations")
    if current.get("schema") != "skeleton.secret_reference_index.v1" or not isinstance(registrations, list):
        raise SecretProviderUnavailable("reference_index_invalid")
    replacement = {
        "service_id": service_id,
        "alias": alias,
        "provider": "bitwarden",
        "reference_id": reference_id,
    }
    filtered = [
        item
        for item in registrations
        if not (
            isinstance(item, dict)
            and item.get("service_id") == service_id
            and item.get("alias") == alias
        )
    ]
    filtered.append(replacement)
    updated = {"schema": "skeleton.secret_reference_index.v1", "registrations": filtered}
    return json.dumps(updated, sort_keys=True, separators=(",", ":")) + "\n"


def _encrypt_registered_reference_index(serialized: str) -> None:
    try:
        result = subprocess.run(
            [
                "sudo",
                "-n",
                "systemd-creds",
                "encrypt",
                "--name=skeleton-secret-reference-index",
                "-",
                str(BITWARDEN_REFERENCE_INDEX_ENCRYPTED_PATH),
            ],
            input=serialized,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SecretProviderUnavailable("reference_index_encrypt_failed") from exc
    if result.returncode != 0:
        raise SecretProviderUnavailable("reference_index_encrypt_failed")


def _write_registered_reference_index(path: Path, serialized: str) -> None:
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
        ) as tmp:
            tmp.write(serialized)
            tmp_path = Path(tmp.name)
        tmp_path.chmod(0o600)
        tmp_path.replace(path)
    except OSError as exc:
        raise SecretProviderUnavailable("reference_index_write_failed") from exc


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
