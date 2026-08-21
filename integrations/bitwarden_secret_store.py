from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from importlib import metadata as importlib_metadata
import json
from pathlib import Path
import shutil
import subprocess

from core.secret_reference import BitwardenIdentifier
from core.secret_store import (
    ResolvedSecret,
    SecretMissing,
    SecretProviderUnavailable,
    SecretReference,
    SecretResolutionContext,
    SecretStore,
)


BITWARDEN_ACCESS_TOKEN_CREDENTIAL = "bitwarden-access-token"
PINNED_BITWARDEN_SDK_PACKAGE = "bitwarden-sdk"
PINNED_BITWARDEN_SDK_VERSION = "2.1.0"


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


class BitwardenSdkIdentifierDiscoveryError(RuntimeError):
    pass


@dataclass(slots=True)
class BitwardenSdkIdentifiersAdapter:
    access_token: str
    sdk_module: object

    @classmethod
    def from_systemd_credentials(
        cls,
        authority_environment: Mapping[str, str],
        *,
        token_credential_name: str = BITWARDEN_ACCESS_TOKEN_CREDENTIAL,
    ) -> "BitwardenSdkIdentifiersAdapter":
        _require_pinned_bitwarden_sdk()
        token = _read_credential(authority_environment, token_credential_name)
        try:
            sdk_module = import_module("bitwarden_sdk")
        except ImportError as exc:
            raise BitwardenSdkIdentifierDiscoveryError("bitwarden_sdk_unavailable") from exc
        return cls(access_token=token, sdk_module=sdk_module)

    def discover_identifiers(self) -> tuple[BitwardenIdentifier, ...]:
        """Use only the official SDK identifiers/list surface; never fetch values."""

        try:
            client = self._client()
            auth_response = self._login_access_token(client)
            organization_id = self._organization_id(client, auth_response)
            response = self._list_secret_identifiers(client, organization_id)
            return tuple(_coerce_identifier(item) for item in _response_data(response))
        except BitwardenSdkIdentifierDiscoveryError:
            raise
        except Exception as exc:
            raise BitwardenSdkIdentifierDiscoveryError("bitwarden_identifier_discovery_failed") from exc

    def __repr__(self) -> str:
        return "BitwardenSdkIdentifiersAdapter(access_token=<redacted>, sdk_module=<redacted>)"

    def _client(self) -> object:
        settings_cls = getattr(self.sdk_module, "ClientSettings", None)
        client_cls = getattr(self.sdk_module, "BitwardenClient", None) or getattr(
            self.sdk_module, "Client", None
        )
        if client_cls is None:
            raise BitwardenSdkIdentifierDiscoveryError("bitwarden_sdk_client_unavailable")
        if settings_cls is None:
            return client_cls()
        return client_cls(settings_cls())

    def _login_access_token(self, client: object) -> object:
        auth = _call_child(client, "auth")
        if auth is None:
            raise BitwardenSdkIdentifierDiscoveryError("bitwarden_sdk_auth_unavailable")
        for method_name in ("login_access_token", "loginAccessToken"):
            method = getattr(auth, method_name, None)
            if callable(method):
                return method(self.access_token)
        request_cls = getattr(self.sdk_module, "AccessTokenLoginRequest", None)
        login = getattr(auth, "login", None)
        if callable(login) and request_cls is not None:
            return login(request_cls(access_token=self.access_token))
        raise BitwardenSdkIdentifierDiscoveryError("bitwarden_sdk_access_token_login_unavailable")

    def _organization_id(self, client: object, auth_response: object) -> object:
        for source in (auth_response, getattr(client, "config", None), getattr(client, "configuration", None)):
            if source is None:
                continue
            for attr in ("organization_id", "organizationId", "organization"):
                value = getattr(source, attr, None)
                if value:
                    return value
            if isinstance(source, Mapping):
                for key in ("organization_id", "organizationId", "organization"):
                    value = source.get(key)
                    if value:
                        return value
        raise BitwardenSdkIdentifierDiscoveryError("bitwarden_organization_id_unavailable")

    def _list_secret_identifiers(self, client: object, organization_id: object) -> object:
        secrets = _call_child(client, "secrets")
        if secrets is None:
            raise BitwardenSdkIdentifierDiscoveryError("bitwarden_sdk_secrets_unavailable")
        for forbidden in ("get", "get_by_ids", "getByIds", "list_by_project", "listByProject"):
            if getattr(secrets, forbidden, None) is _ForbiddenValueBearingMethod:
                raise BitwardenSdkIdentifierDiscoveryError("bitwarden_sdk_value_method_armed")
        request_cls = getattr(self.sdk_module, "SecretIdentifiersRequest", None)
        request = request_cls(organization_id=organization_id) if request_cls is not None else organization_id
        for method_name in ("list", "list_identifiers", "listIdentifiers"):
            method = getattr(secrets, method_name, None)
            if callable(method):
                return method(request)
        raise BitwardenSdkIdentifierDiscoveryError("bitwarden_sdk_identifiers_list_unavailable")


class _ForbiddenValueBearingMethod:
    pass


def _require_pinned_bitwarden_sdk() -> None:
    try:
        version = importlib_metadata.version(PINNED_BITWARDEN_SDK_PACKAGE)
    except importlib_metadata.PackageNotFoundError as exc:
        raise BitwardenSdkIdentifierDiscoveryError("bitwarden_sdk_unavailable") from exc
    if version != PINNED_BITWARDEN_SDK_VERSION:
        raise BitwardenSdkIdentifierDiscoveryError("bitwarden_sdk_version_mismatch")


def _call_child(parent: object, name: str) -> object:
    child = getattr(parent, name, None)
    return child() if callable(child) else child


def _response_data(response: object) -> tuple[object, ...]:
    if isinstance(response, Mapping):
        data = response.get("data")
    else:
        data = getattr(response, "data", None)
    if not isinstance(data, (list, tuple)):
        raise BitwardenSdkIdentifierDiscoveryError("bitwarden_identifier_response_invalid")
    return tuple(data)


def _coerce_identifier(item: object) -> BitwardenIdentifier:
    if isinstance(item, Mapping):
        identifier = item.get("id")
        key = item.get("key")
    else:
        identifier = getattr(item, "id", None)
        key = getattr(item, "key", None)
    if not isinstance(identifier, str) or not isinstance(key, str):
        raise BitwardenSdkIdentifierDiscoveryError("bitwarden_identifier_response_invalid")
    return BitwardenIdentifier(id=identifier, key=key)


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
