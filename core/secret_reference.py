from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import re

from core.secret_store import InvalidSecretReference, SecretProviderUnavailable, SecretReference


REFERENCE_INDEX_CREDENTIAL_NAME = "skeleton-secret-reference-index"
REFERENCE_BOOTSTRAP_REQUIRED = "REFERENCE_BOOTSTRAP_REQUIRED"
_BITWARDEN_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class SecretReferenceRegistrationError(ValueError):
    pass


def registered_bitwarden_reference_from_systemd_index(
    authority_environment: Mapping[str, str],
    *,
    service_id: str,
    alias: str,
    bootstrap_required: bool = True,
    fallback_credential_name: str | None = None,
    index_credential_name: str = REFERENCE_INDEX_CREDENTIAL_NAME,
) -> SecretReference:
    """Return exactly one code-registered opaque Bitwarden reference.

    The index contains only public-safe binding metadata and opaque provider
    references. It is intentionally not a vault query surface and never reads
    unrelated provider values.
    """

    del fallback_credential_name
    try:
        raw_reference = _read_index_reference(
            authority_environment,
            service_id=service_id,
            alias=alias,
            index_credential_name=index_credential_name,
        )
    except SecretProviderUnavailable:
        if bootstrap_required:
            raise SecretReferenceRegistrationError(REFERENCE_BOOTSTRAP_REQUIRED) from None
        raise
    try:
        return SecretReference(provider="bitwarden", reference_id=raw_reference)
    except InvalidSecretReference as exc:
        raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID") from exc


def _read_index_reference(
    authority_environment: Mapping[str, str],
    *,
    service_id: str,
    alias: str,
    index_credential_name: str,
) -> str:
    payload = _read_systemd_credential(authority_environment, index_credential_name)
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID") from exc
    if not isinstance(decoded, Mapping):
        raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
    if set(decoded) - {"schema", "registrations"}:
        raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
    if decoded.get("schema") != "skeleton.secret_reference_index.v1":
        raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
    registrations = decoded.get("registrations")
    if not isinstance(registrations, list) or len(registrations) > 32:
        raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")

    matches: list[str] = []
    for item in registrations:
        if not isinstance(item, Mapping):
            raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
        allowed = {"service_id", "alias", "provider", "reference_id"}
        if set(item) - allowed:
            raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
        if item.get("service_id") != service_id or item.get("alias") != alias:
            continue
        if item.get("provider") != "bitwarden":
            raise SecretReferenceRegistrationError("REFERENCE_PROVIDER_UNSUPPORTED")
        reference_id = item.get("reference_id")
        if not isinstance(reference_id, str) or not _BITWARDEN_UUID_RE.fullmatch(reference_id):
            raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
        matches.append(reference_id.lower())

    if not matches:
        raise SecretReferenceRegistrationError(REFERENCE_BOOTSTRAP_REQUIRED)
    if len(set(matches)) != 1 or len(matches) != 1:
        raise SecretReferenceRegistrationError("REFERENCE_REGISTRATION_AMBIGUOUS")
    return matches[0]


def _read_systemd_credential(authority_environment: Mapping[str, str], name: str) -> str:
    directory = authority_environment.get("CREDENTIALS_DIRECTORY", "").strip()
    if not directory or not Path(directory).is_absolute():
        raise SecretProviderUnavailable("systemd_credentials_directory_unavailable")
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise SecretProviderUnavailable("invalid_systemd_credential_name")
    path = Path(directory) / name
    if not path.is_file():
        raise SecretProviderUnavailable("systemd_credential_unavailable")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise SecretProviderUnavailable("systemd_credential_read_failed") from exc
    if not value:
        raise SecretProviderUnavailable("systemd_credential_empty")
    return value
