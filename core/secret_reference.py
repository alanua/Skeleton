from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess

from core.secret_store import InvalidSecretReference, SecretProviderUnavailable, SecretReference


REFERENCE_INDEX_CREDENTIAL_NAME = "skeleton-secret-reference-index"
REFERENCE_BOOTSTRAP_REQUIRED = "REFERENCE_BOOTSTRAP_REQUIRED"
REFERENCE_INDEX_SCHEMA = "skeleton.secret_reference_index.v1"
GMAIL_PRIMARY_SERVICE_ID = "mail-gmail"
GMAIL_PRIMARY_ALIAS = "acct:gmail-primary"

_GMAIL_PRIMARY_REQUIRED_TOKENS = frozenset({"gmail", "primary", "oauth"})
_GMAIL_PRIMARY_REJECT_TOKENS = frozenset(
    {
        "backup",
        "bak",
        "dev",
        "old",
        "previous",
        "sandbox",
        "secondary",
        "staging",
        "test",
        "tmp",
    }
)


class SecretReferenceRegistrationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BitwardenIdentifier:
    id: str
    key: str


@dataclass(frozen=True, slots=True)
class ReferenceBootstrapResult:
    status: str
    match_count_class: str
    persisted: bool
    reason: str

    def public_receipt(self) -> dict[str, object]:
        return {
            "schema": "skeleton.reference_bootstrap_receipt.v1",
            "status": self.status,
            "match_count_class": self.match_count_class,
            "persisted": self.persisted,
            "public_safe": True,
            "private_payloads_included": False,
            "reason": self.reason,
        }


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
    if decoded.get("schema") != REFERENCE_INDEX_SCHEMA:
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
        if not isinstance(reference_id, str):
            raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
        matches.append(reference_id)

    if not matches:
        raise SecretReferenceRegistrationError(REFERENCE_BOOTSTRAP_REQUIRED)
    if len(set(matches)) != 1 or len(matches) != 1:
        raise SecretReferenceRegistrationError("REFERENCE_REGISTRATION_AMBIGUOUS")
    return matches[0]


def bootstrap_gmail_primary_reference_index(
    authority_environment: Mapping[str, str],
    *,
    identifiers: tuple[BitwardenIdentifier, ...],
    systemd_creds_path: str = "systemd-creds",
    encrypted_credential_dir: str = "/etc/credstore.encrypted",
    run=subprocess.run,
) -> ReferenceBootstrapResult:
    """Persist the one Gmail-primary Bitwarden UUID using the encrypted credential path."""

    matches = match_gmail_primary_bitwarden_identifiers(identifiers)
    match_class = _match_count_class(len(matches))
    if len(matches) != 1:
        return ReferenceBootstrapResult(
            status="BLOCKED",
            match_count_class=match_class,
            persisted=False,
            reason="NO_ELIGIBLE_IDENTIFIER" if not matches else "AMBIGUOUS_IDENTIFIER",
        )

    payload = _reference_index_payload_with_registration(
        authority_environment,
        service_id=GMAIL_PRIMARY_SERVICE_ID,
        alias=GMAIL_PRIMARY_ALIAS,
        provider="bitwarden",
        reference_id=matches[0].id,
        index_credential_name=REFERENCE_INDEX_CREDENTIAL_NAME,
    )
    destination = Path(encrypted_credential_dir) / REFERENCE_INDEX_CREDENTIAL_NAME
    try:
        result = run(
            [
                "sudo",
                "-n",
                systemd_creds_path,
                "encrypt",
                f"--name={REFERENCE_INDEX_CREDENTIAL_NAME}",
                "-",
                str(destination),
            ],
            input=json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ReferenceBootstrapResult(
            status="BLOCKED",
            match_count_class=match_class,
            persisted=False,
            reason="SYSTEMD_CREDENTIAL_ENCRYPT_FAILED",
        )
    if result.returncode != 0:
        return ReferenceBootstrapResult(
            status="BLOCKED",
            match_count_class=match_class,
            persisted=False,
            reason="SYSTEMD_CREDENTIAL_ENCRYPT_FAILED",
        )
    return ReferenceBootstrapResult(
        status="PASS",
        match_count_class=match_class,
        persisted=True,
        reason="OK",
    )


def match_gmail_primary_bitwarden_identifiers(
    identifiers: tuple[BitwardenIdentifier, ...],
) -> tuple[BitwardenIdentifier, ...]:
    eligible: list[BitwardenIdentifier] = []
    for item in identifiers:
        tokens = _identifier_key_tokens(item.key)
        if _GMAIL_PRIMARY_REQUIRED_TOKENS <= tokens and not tokens.intersection(
            _GMAIL_PRIMARY_REJECT_TOKENS
        ):
            eligible.append(item)
    return tuple(sorted(eligible, key=lambda item: item.key.casefold()))


def _identifier_key_tokens(key: str) -> frozenset[str]:
    return frozenset(token for token in re.split(r"[^a-z0-9]+", key.casefold()) if token)


def _match_count_class(count: int) -> str:
    if count == 0:
        return "zero"
    if count == 1:
        return "one"
    return "many"


def _reference_index_payload_with_registration(
    authority_environment: Mapping[str, str],
    *,
    service_id: str,
    alias: str,
    provider: str,
    reference_id: str,
    index_credential_name: str,
) -> dict[str, object]:
    try:
        payload = _read_systemd_credential(authority_environment, index_credential_name)
    except SecretProviderUnavailable:
        decoded: dict[str, object] = {"schema": REFERENCE_INDEX_SCHEMA, "registrations": []}
    else:
        try:
            existing = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID") from exc
        if not isinstance(existing, dict):
            raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
        _validate_reference_index(existing)
        decoded = existing

    registrations = decoded.get("registrations")
    if not isinstance(registrations, list):
        raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
    next_registrations = [
        item
        for item in registrations
        if not (
            isinstance(item, Mapping)
            and item.get("service_id") == service_id
            and item.get("alias") == alias
        )
    ]
    next_registrations.append(
        {
            "service_id": service_id,
            "alias": alias,
            "provider": provider,
            "reference_id": SecretReference(provider=provider, reference_id=reference_id).reference_id,
        }
    )
    if len(next_registrations) > 32:
        raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
    return {"schema": REFERENCE_INDEX_SCHEMA, "registrations": next_registrations}


def _validate_reference_index(decoded: Mapping[str, object]) -> None:
    if set(decoded) - {"schema", "registrations"}:
        raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
    if decoded.get("schema") != REFERENCE_INDEX_SCHEMA:
        raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
    registrations = decoded.get("registrations")
    if not isinstance(registrations, list) or len(registrations) > 32:
        raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
    for item in registrations:
        if not isinstance(item, Mapping):
            raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
        if set(item) - {"service_id", "alias", "provider", "reference_id"}:
            raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
        if item.get("provider") != "bitwarden":
            raise SecretReferenceRegistrationError("REFERENCE_PROVIDER_UNSUPPORTED")
        reference_id = item.get("reference_id")
        if not isinstance(reference_id, str):
            raise SecretReferenceRegistrationError("REFERENCE_INDEX_INVALID")
        SecretReference(provider="bitwarden", reference_id=reference_id)


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
