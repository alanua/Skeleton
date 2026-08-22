from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import integrations.bitwarden_secret_store as bitwarden
from core.secret_store import SecretProviderUnavailable, SecretReference, SecretResolutionContext
from integrations.bitwarden_secret_store import (
    BitwardenReferenceDiscoveryError,
    BwsCliSecretsManagerStore,
    derive_bitwarden_organization_id_from_machine_token,
    discover_gmail_primary_reference_with_sdk,
    public_reference_discovery_receipt,
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
    payload = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"header.{payload}.signature"


def test_identity_exchange_derives_org_id_from_fixed_endpoint(monkeypatch) -> None:
    org_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    observed: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(
                {"access_token": _jwt_with_claims({"accesssecretsmanager": org_id})}
            ).encode("utf-8")

    def fake_urlopen(request, *, timeout):
        observed["url"] = request.full_url
        observed["data"] = request.data.decode("ascii")
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setattr(bitwarden.urllib.request, "urlopen", fake_urlopen)

    assert (
        derive_bitwarden_organization_id_from_machine_token(
            "0.synthetic-client.synthetic-secret"
        )
        == org_id
    )
    assert observed["url"] == "https://identity.bitwarden.com/connect/token"
    assert "synthetic-secret" not in observed["url"]
    assert "client_id=synthetic-client" in observed["data"]


def test_identity_exchange_rejects_untrusted_endpoint() -> None:
    with pytest.raises(BitwardenReferenceDiscoveryError, match="identity_endpoint_untrusted"):
        derive_bitwarden_organization_id_from_machine_token(
            "0.client.secret",
            identity_url="https://example.com",
        )


def test_sdk_identifier_discovery_uses_isolated_pinned_list_only(monkeypatch) -> None:
    observed: dict[str, object] = {}
    selected = "11111111-2222-3333-4444-555555555555"

    def fake_run(argv, **kwargs):
        observed["argv"] = list(argv)
        observed["env"] = dict(kwargs["env"])
        observed["script"] = argv[-1]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"matches": [selected]}),
            stderr="",
        )

    monkeypatch.setattr(bitwarden.subprocess, "run", fake_run)

    assert discover_gmail_primary_reference_with_sdk(
        sdk_python="/opt/skeleton-bitwarden-sdk/venv/bin/python3",
        access_token="0.client.secret",
        organization_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    ) == selected
    assert observed["argv"][:3] == ["/opt/skeleton-bitwarden-sdk/venv/bin/python3", "-I", "-c"]
    assert "0.client.secret" not in " ".join(observed["argv"])
    assert observed["env"]["BWS_ACCESS_TOKEN"] == "0.client.secret"
    assert 'version("bitwarden-sdk") != EXPECTED_VERSION' in observed["script"]
    assert "secrets_client.list(organization_id)" in observed["script"]
    assert "secrets_client.get" not in observed["script"]
    assert "get_by_ids" not in observed["script"]
    assert "sync(" not in observed["script"]


@pytest.mark.parametrize(
    ("matches", "reason"),
    [
        ([], "gmail_primary_reference_zero_matches"),
        (
            [
                "11111111-2222-3333-4444-555555555555",
                "66666666-7777-8888-9999-000000000000",
            ],
            "gmail_primary_reference_many_matches",
        ),
    ],
)
def test_sdk_identifier_discovery_fails_closed_for_zero_or_many(
    matches: list[str],
    reason: str,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        bitwarden.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"matches": matches}),
            stderr="",
        ),
    )

    with pytest.raises(BitwardenReferenceDiscoveryError, match=reason):
        discover_gmail_primary_reference_with_sdk(
            sdk_python="/opt/skeleton-bitwarden-sdk/venv/bin/python3",
            access_token="0.client.secret",
            organization_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )


def test_public_reference_discovery_receipt_has_only_public_match_state() -> None:
    receipt = public_reference_discovery_receipt(
        status="PASS",
        reason="OK",
        match_count=1,
    )

    assert receipt == {
        "status": "PASS",
        "reason": "OK",
        "zero_matches": False,
        "one_match": True,
        "many_matches": False,
        "secret_values_exposed": False,
        "credential_directory_written": False,
    }
