from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.secret_reference import (
    GOOGLE_SHEETS_CREDENTIALS,
    ApprovedSecretResolution,
    SecretReference,
    SecretReferenceError,
    SecretScope,
    resolve_secret_reference,
)
from tools.skeleton_core.private_contact_import import AUTH_INVALID, AUTH_MISSING, check_auth


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "secret_reference.schema.json"


class EphemeralCredentials:
    def with_scopes(self, scopes: tuple[str, ...]) -> "EphemeralCredentials":
        self.scopes = scopes
        return self


def _reference(*, branch: str = "runner/typed-secret-reference-boundary") -> SecretReference:
    return SecretReference(
        secret_type=GOOGLE_SHEETS_CREDENTIALS,
        scope=SecretScope(
            project="skeleton",
            dataset="private_contact_import",
            repo="alanua/Skeleton",
            branch=branch,
            task="typed-secret-reference-462b9a1d-v1",
        ),
    )


def test_secret_reference_schema_matches_model_shape() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    reference = _reference()

    assert schema["$id"] == "skeleton.secret_reference.schema.json"
    assert schema["properties"]["schema"]["const"] == "skeleton.secret_reference.v1"
    assert set(schema["properties"]["scope"]["required"]) == {"project", "dataset", "repo", "branch", "task"}
    assert reference.to_dict()["scope"]["repo"] == "alanua/Skeleton"


def test_secret_reference_rejects_plaintext_path_env_and_extra_fields() -> None:
    payload = _reference().to_dict()
    payload["credentials_path"] = "/tmp/service-account.json"
    with pytest.raises(SecretReferenceError, match="non-typed fields"):
        SecretReference.from_dict(payload)

    with pytest.raises(SecretReferenceError, match="credential material"):
        SecretScope(
            project="skeleton",
            dataset="private_contact_import",
            repo="alanua/Skeleton",
            branch="GOOGLE_APPLICATION_CREDENTIALS",
            task="typed-secret-reference-462b9a1d-v1",
        )


def test_resolver_requires_approved_exact_scope_adapter_result() -> None:
    reference = _reference()
    material = EphemeralCredentials()
    approved = ApprovedSecretResolution(
        reference=reference,
        material=material,
        approved=True,
        adapter="unit-test-approved-adapter",
    )

    assert resolve_secret_reference(reference, approved) is material

    mismatched = ApprovedSecretResolution(
        reference=_reference(branch="main"),
        material=EphemeralCredentials(),
        approved=True,
        adapter="unit-test-approved-adapter",
    )
    with pytest.raises(SecretReferenceError, match="scope does not match"):
        resolve_secret_reference(reference, mismatched)

    unapproved = ApprovedSecretResolution(
        reference=reference,
        material=EphemeralCredentials(),
        approved=False,
        adapter="unit-test-approved-adapter",
    )
    with pytest.raises(SecretReferenceError, match="not approved"):
        resolve_secret_reference(reference, unapproved)


@pytest.mark.parametrize("material", ["secret-json", b"secret-json", {"client_email": "private"}, Path("/tmp/key.json")])
def test_resolver_rejects_plaintext_path_and_json_like_material(material: object) -> None:
    with pytest.raises(SecretReferenceError, match="ephemeral credential object"):
        ApprovedSecretResolution(
            reference=_reference(),
            material=material,
            approved=True,
            adapter="unit-test-approved-adapter",
        )


def test_contact_import_default_auth_ignores_google_application_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/nonexistent-service-account.json")

    status, reason = check_auth()

    assert status == AUTH_MISSING
    assert reason == "Google Sheets credentials are not provisioned on this runner."


def test_contact_import_rejects_unapproved_typed_reference() -> None:
    status, reason = check_auth(credentials_reference=_reference(), credentials_resolution=None)

    assert status == AUTH_INVALID
    assert reason == "Google Sheets credentials reference was not approved by the resolver boundary."
