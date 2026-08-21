from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.secret_reference import (
    BitwardenIdentifier,
    bootstrap_gmail_primary_reference_index,
    match_gmail_primary_bitwarden_identifiers,
)
import integrations.bitwarden_secret_store as bitwarden
from core.secret_store import SecretProviderUnavailable, SecretReference, SecretResolutionContext
from integrations.bitwarden_secret_store import (
    BwsCliSecretsManagerStore,
    BitwardenSdkIdentifierDiscoveryError,
    BitwardenSdkIdentifiersAdapter,
    bitwarden_reference_from_systemd_credential,
)


def _authority(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    token_file = credentials / "bitwarden-access-token"
    token_file.write_text("synthetic-machine-token\n", encoding="utf-8")
    ref_file = credentials / "openrouter-secret-ref"
    ref_file.write_text("11111111-2222-3333-4444-555555555555\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    bws = bin_dir / "bws"
    bws.write_text("", encoding="utf-8")
    return (
        {"HOME": str(tmp_path), "PATH": str(bin_dir), "CREDENTIALS_DIRECTORY": str(credentials)},
        bws,
        ref_file,
    )


def test_systemd_reference_contains_only_bitwarden_id(tmp_path: Path) -> None:
    authority, _bws, _ref_file = _authority(tmp_path)
    reference = bitwarden_reference_from_systemd_credential(authority, "openrouter-secret-ref")
    assert reference == SecretReference(
        provider="bitwarden",
        reference_id="11111111-2222-3333-4444-555555555555",
    )


def test_bws_cli_resolves_exact_secret_without_token_in_argv(tmp_path: Path, monkeypatch) -> None:
    authority, bws, _ref_file = _authority(tmp_path)
    monkeypatch.setattr(bitwarden.shutil, "which", lambda name, *, path=None: str(bws) if name == "bws" else None)
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = list(argv)
        observed["env"] = dict(kwargs["env"])
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "object": "secret",
                    "id": "11111111-2222-3333-4444-555555555555",
                    "key": "OPENROUTER_API_KEY",
                    "value": "synthetic-openrouter-key",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(bitwarden.subprocess, "run", fake_run)
    store = BwsCliSecretsManagerStore.from_systemd_credentials(authority)
    material = store.resolve(
        SecretReference(provider="bitwarden", reference_id="11111111-2222-3333-4444-555555555555"),
        SecretResolutionContext(
            machine_identity="hetzner-agent-runner-1",
            audience="openhands-openrouter",
            task_kind="code_generation",
        ),
    )
    child = material.inject({}, "LLM_API_KEY")

    assert child["LLM_API_KEY"] == "synthetic-openrouter-key"
    assert "synthetic-machine-token" not in " ".join(observed["argv"])
    assert observed["argv"] == [
        str(bws.resolve()),
        "secret",
        "get",
        "11111111-2222-3333-4444-555555555555",
        "--output",
        "json",
    ]
    assert observed["env"]["BWS_ACCESS_TOKEN"] == "synthetic-machine-token"
    assert "synthetic-machine-token" not in repr(store)
    assert "synthetic-openrouter-key" not in repr(material)


def test_bws_cli_fails_closed_without_machine_credential_or_cli(tmp_path: Path, monkeypatch) -> None:
    authority, _bws, _ref_file = _authority(tmp_path)
    monkeypatch.setattr(bitwarden.shutil, "which", lambda name, *, path=None: None)
    with pytest.raises(SecretProviderUnavailable):
        BwsCliSecretsManagerStore.from_systemd_credentials(authority)

    authority["CREDENTIALS_DIRECTORY"] = str(tmp_path / "missing")
    with pytest.raises(SecretProviderUnavailable):
        bitwarden_reference_from_systemd_credential(authority, "openrouter-secret-ref")


def test_bws_cli_rejects_reference_mismatch_and_nonzero_exit(tmp_path: Path, monkeypatch) -> None:
    authority, bws, _ref_file = _authority(tmp_path)
    monkeypatch.setattr(bitwarden.shutil, "which", lambda name, *, path=None: str(bws))
    store = BwsCliSecretsManagerStore.from_systemd_credentials(authority)
    context = SecretResolutionContext(
        machine_identity="hetzner-agent-runner-1",
        audience="openhands-openrouter",
        task_kind="code_generation",
    )
    reference = SecretReference(provider="bitwarden", reference_id="11111111-2222-3333-4444-555555555555")

    monkeypatch.setattr(
        bitwarden.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='{"id":"other","value":"secret"}', stderr=""),
    )
    with pytest.raises(SecretProviderUnavailable, match="reference_mismatch"):
        store.resolve(reference, context)

    monkeypatch.setattr(
        bitwarden.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="auth failed"),
    )
    with pytest.raises(SecretProviderUnavailable, match="secret_get_failed"):
        store.resolve(reference, context)


def test_gmail_primary_identifier_matcher_accepts_unique_and_rejects_decoys() -> None:
    identifiers = (
        BitwardenIdentifier(
            id="11111111-2222-3333-4444-555555555555",
            key="skeleton/gmail/primary/oauth",
        ),
        BitwardenIdentifier(
            id="22222222-3333-4444-5555-666666666666",
            key="skeleton/gmail/secondary/oauth",
        ),
        BitwardenIdentifier(
            id="33333333-4444-5555-6666-777777777777",
            key="test gmail primary oauth",
        ),
        BitwardenIdentifier(
            id="44444444-5555-6666-7777-888888888888",
            key="old gmail primary oauth",
        ),
    )

    assert match_gmail_primary_bitwarden_identifiers(identifiers) == (identifiers[0],)


def test_gmail_primary_bootstrap_fails_closed_on_none_and_ambiguous(tmp_path: Path) -> None:
    calls: list[object] = []

    none = bootstrap_gmail_primary_reference_index(
        {"CREDENTIALS_DIRECTORY": str(tmp_path / "missing")},
        identifiers=(),
        run=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    assert none.public_receipt()["status"] == "BLOCKED"
    assert none.public_receipt()["match_count_class"] == "zero"
    assert none.public_receipt()["reason"] == "NO_ELIGIBLE_IDENTIFIER"

    ambiguous = bootstrap_gmail_primary_reference_index(
        {"CREDENTIALS_DIRECTORY": str(tmp_path / "missing")},
        identifiers=(
            BitwardenIdentifier(
                id="11111111-2222-3333-4444-555555555555",
                key="skeleton gmail primary oauth",
            ),
            BitwardenIdentifier(
                id="66666666-7777-8888-9999-000000000000",
                key="prod gmail primary oauth bundle",
            ),
        ),
        run=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    assert ambiguous.public_receipt()["status"] == "BLOCKED"
    assert ambiguous.public_receipt()["match_count_class"] == "many"
    assert ambiguous.public_receipt()["reason"] == "AMBIGUOUS_IDENTIFIER"
    assert calls == []


def test_gmail_primary_bootstrap_persists_only_encrypted_reference_index(tmp_path: Path) -> None:
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = list(argv)
        observed["input"] = kwargs["input"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = bootstrap_gmail_primary_reference_index(
        {"CREDENTIALS_DIRECTORY": str(credentials)},
        identifiers=(
            BitwardenIdentifier(
                id="11111111-2222-3333-4444-555555555555",
                key="skeleton gmail primary oauth",
            ),
        ),
        encrypted_credential_dir=str(tmp_path / "credstore.encrypted"),
        run=fake_run,
    )

    assert result.public_receipt() == {
        "schema": "skeleton.reference_bootstrap_receipt.v1",
        "status": "PASS",
        "match_count_class": "one",
        "persisted": True,
        "public_safe": True,
        "private_payloads_included": False,
        "reason": "OK",
    }
    assert observed["argv"][:5] == [
        "sudo",
        "-n",
        "systemd-creds",
        "encrypt",
        "--name=skeleton-secret-reference-index",
    ]
    assert json.loads(observed["input"]) == {
        "schema": "skeleton.secret_reference_index.v1",
        "registrations": [
            {
                "service_id": "mail-gmail",
                "alias": "acct:gmail-primary",
                "provider": "bitwarden",
                "reference_id": "11111111-2222-3333-4444-555555555555",
            }
        ],
    }


def test_sdk_identifier_adapter_lists_identifiers_without_value_methods(monkeypatch, tmp_path: Path) -> None:
    authority, _bws, _ref_file = _authority(tmp_path)
    calls: list[str] = []

    class SecretIdentifiersRequest:
        def __init__(self, *, organization_id):
            self.organization_id = organization_id

    class Auth:
        def login_access_token(self, token):
            calls.append("login")
            assert token == "synthetic-machine-token"
            return SimpleNamespace(organization_id="99999999-aaaa-bbbb-cccc-dddddddddddd")

    class Secrets:
        def list(self, request):
            calls.append(f"list:{request.organization_id}")
            return SimpleNamespace(
                data=[
                    SimpleNamespace(
                        id="11111111-2222-3333-4444-555555555555",
                        key="skeleton gmail primary oauth",
                    )
                ]
            )

        def get(self, _reference_id):
            raise AssertionError("value-bearing get must not be called")

        def get_by_ids(self, _reference_ids):
            raise AssertionError("value-bearing get_by_ids must not be called")

    class Client:
        def auth(self):
            return Auth()

        def secrets(self):
            return Secrets()

    sdk_module = SimpleNamespace(
        BitwardenClient=lambda _settings: Client(),
        ClientSettings=lambda: SimpleNamespace(),
        SecretIdentifiersRequest=SecretIdentifiersRequest,
    )
    monkeypatch.setattr(bitwarden.importlib_metadata, "version", lambda _package: "2.1.0")
    monkeypatch.setattr(bitwarden, "import_module", lambda _name: sdk_module)

    adapter = BitwardenSdkIdentifiersAdapter.from_systemd_credentials(authority)
    assert adapter.discover_identifiers() == (
        BitwardenIdentifier(
            id="11111111-2222-3333-4444-555555555555",
            key="skeleton gmail primary oauth",
        ),
    )
    assert calls == ["login", "list:99999999-aaaa-bbbb-cccc-dddddddddddd"]


def test_sdk_identifier_adapter_fails_before_discovery_on_unpinned_dependency(
    monkeypatch,
    tmp_path: Path,
) -> None:
    authority, _bws, _ref_file = _authority(tmp_path)
    imported: list[str] = []
    monkeypatch.setattr(bitwarden.importlib_metadata, "version", lambda _package: "2.0.0")
    monkeypatch.setattr(bitwarden, "import_module", lambda name: imported.append(name))

    with pytest.raises(BitwardenSdkIdentifierDiscoveryError, match="version_mismatch"):
        BitwardenSdkIdentifiersAdapter.from_systemd_credentials(authority)

    assert imported == []
