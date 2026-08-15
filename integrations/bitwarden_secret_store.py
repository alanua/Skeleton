from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess

from core.secret_store import (
    ResolvedSecret,
    SecretMissing,
    SecretProviderUnavailable,
    SecretReference,
    SecretResolutionContext,
    SecretStore,
)


BITWARDEN_ACCESS_TOKEN_CREDENTIAL = "bitwarden-access-token"


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
