from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import integrations.bitwarden_secret_store as bitwarden
from scripts import bitwarden_gmail_reference_helper as helper
from core.secret_store import SecretProviderUnavailable, SecretReference, SecretResolutionContext
from integrations.bitwarden_secret_store import (
    BwsCliSecretsManagerStore,
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


def _jwt_with_claims(claims: dict[str, object]) -> str:
    payload = json.dumps(claims, separators=(",", ":")).encode("utf-8")
    encoded = helper.base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"header.{encoded}.signature"


def test_bitwarden_identity_helper_uses_fixed_endpoint_and_extracts_only_org_claim(monkeypatch) -> None:
    org_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    observed: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps({"access_token": _jwt_with_claims({"organizationId": org_id})}).encode("utf-8")

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["method"] = request.get_method()
        observed["body"] = request.data.decode("ascii")
        observed["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(helper.urllib.request, "urlopen", fake_urlopen)

    result = helper.organization_id_from_machine_token(
        "0.11111111-2222-3333-4444-555555555555.synthetic-client-secret:synthetic-key"
    )

    assert result == org_id
    assert observed["url"] == helper.IDENTITY_TOKEN_URL
    assert observed["method"] == "POST"
    assert "grant_type=client_credentials" in observed["body"]
    assert "scope=api.secrets" in observed["body"]


def test_bitwarden_gmail_helper_lists_metadata_only_and_fails_closed(monkeypatch) -> None:
    org_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    secret_id = "11111111-2222-3333-4444-555555555555"
    opened: list[str] = []

    class FakeResponse:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        del timeout
        opened.append(request.full_url)
        if request.full_url == helper.IDENTITY_TOKEN_URL:
            return FakeResponse({"access_token": _jwt_with_claims({"organizationId": org_id})})
        assert request.full_url == f"{helper.SECRETS_METADATA_URL}?organizationId={org_id}"
        return FakeResponse(
            [
                {
                    "id": secret_id,
                    "organizationId": org_id,
                    "key": helper.GMAIL_PRIMARY_KEY,
                }
            ]
        )

    monkeypatch.setattr(helper.urllib.request, "urlopen", fake_urlopen)

    index = helper.build_gmail_primary_reference_index(
        "0.11111111-2222-3333-4444-555555555555.synthetic-client-secret:synthetic-key"
    )

    assert index == {
        "schema": "skeleton.secret_reference_index.v1",
        "registrations": [
            {
                "service_id": "mail-gmail",
                "alias": "acct:gmail-primary",
                "provider": "bitwarden",
                "reference_id": secret_id,
            }
        ],
    }
    assert opened == [
        helper.IDENTITY_TOKEN_URL,
        f"{helper.SECRETS_METADATA_URL}?organizationId={org_id}",
    ]


def test_bitwarden_gmail_helper_rejects_value_bearing_or_ambiguous_discovery() -> None:
    org_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    with pytest.raises(helper.HelperError, match="metadata_contains_value_fields"):
        helper._match_gmail_primary_reference(
            [
                {
                    "id": "11111111-2222-3333-4444-555555555555",
                    "organizationId": org_id,
                    "key": helper.GMAIL_PRIMARY_KEY,
                    "value": "SECRET",
                }
            ],
            org_id,
        )

    with pytest.raises(helper.HelperError, match="many_matches"):
        helper._match_gmail_primary_reference(
            [
                {
                    "id": "11111111-2222-3333-4444-555555555555",
                    "organizationId": org_id,
                    "key": helper.GMAIL_PRIMARY_KEY,
                },
                {
                    "id": "66666666-7777-8888-9999-000000000000",
                    "organizationId": org_id,
                    "key": helper.GMAIL_PRIMARY_KEY,
                },
            ],
            org_id,
        )


def test_bitwarden_discovery_static_guard_avoids_value_bearing_apis() -> None:
    source = Path("scripts/bitwarden_gmail_reference_helper.py").read_text(encoding="utf-8")
    forbidden = (
        "secret list",
        "secret get",
        "get_by_ids",
        ".sync(",
        "export",
        "run --",
    )
    assert all(token not in source for token in forbidden)
