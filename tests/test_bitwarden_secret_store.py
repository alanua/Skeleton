from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import integrations.bitwarden_secret_store as bitwarden
import scripts.bitwarden_gmail_primary_reference_helper as helper
from core.secret_store import SecretProviderUnavailable, SecretReference, SecretResolutionContext
from integrations.bitwarden_secret_store import (
    BwsCliSecretsManagerStore,
    bitwarden_identity_oauth_form,
    bitwarden_reference_from_systemd_credential,
    parse_bitwarden_machine_token,
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


def test_bitwarden_machine_token_parses_documented_shape_and_oauth_excludes_encryption_key() -> None:
    token = (
        "0."
        "bwa_clientid_1234567890."
        "bws_clientsecret_1234567890abcdef:"
        "enc_key_1234567890abcdefABCDEF=="
    )
    parsed = parse_bitwarden_machine_token(token)
    body = bitwarden_identity_oauth_form(parsed)

    assert parsed.original == token
    assert parsed.client_id == "bwa_clientid_1234567890"
    assert parsed.client_secret == "bws_clientsecret_1234567890abcdef"
    assert parsed.encryption_key == "enc_key_1234567890abcdefABCDEF=="
    assert b"client_id=bwa_clientid_1234567890" in body
    assert b"client_secret=bws_clientsecret_1234567890abcdef" in body
    assert b"enc_key" not in body
    assert b"%3Aenc_key" not in body


@pytest.mark.parametrize(
    "token",
    [
        "0.bwa_clientid_1234567890.bws_clientsecret_1234567890abcdef",
        "0.bwa_clientid_1234567890.bws_clientsecret_1234567890abcdef:",
        "1.bwa_clientid_1234567890.bws_clientsecret_1234567890abcdef:enc_key_1234567890abcdef",
        " 0.bwa_clientid_1234567890.bws_clientsecret_1234567890abcdef:enc_key_1234567890abcdef",
    ],
)
def test_bitwarden_machine_token_malformed_or_missing_encryption_key_fails_closed(token: str) -> None:
    with pytest.raises(ValueError, match="bitwarden_machine_token_invalid"):
        parse_bitwarden_machine_token(token)


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


class _FakeSecrets:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[str] = []

    def list(self, organization_id):
        self.calls.append(organization_id)
        return SimpleNamespace(data=self.rows)

    def get(self, _secret_id):  # pragma: no cover - must not be called.
        raise AssertionError("secret value retrieval is forbidden")

    def get_by_ids(self, _secret_ids):  # pragma: no cover - must not be called.
        raise AssertionError("secret value retrieval is forbidden")

    def sync(self, _organization_id):  # pragma: no cover - must not be called.
        raise AssertionError("secret sync is forbidden")


class _FakeClient:
    def __init__(self, rows, logins):
        self._secrets = _FakeSecrets(rows)
        self._logins = logins

    def auth(self):
        return SimpleNamespace(login_access_token=self._logins.append)

    def secrets(self):
        return self._secrets


def _helper_authority(tmp_path: Path, token: str) -> dict[str, str]:
    credentials = tmp_path / "credentials"
    credentials.mkdir(parents=True)
    (credentials / "bitwarden-access-token").write_text(token, encoding="utf-8")
    (credentials / "bitwarden-organization-id").write_text(
        "00000000-0000-0000-0000-000000000000",
        encoding="utf-8",
    )
    return {"CREDENTIALS_DIRECTORY": str(credentials)}


def test_bitwarden_helper_uses_sdk_login_original_token_and_metadata_only_list(tmp_path: Path) -> None:
    token = (
        "0."
        "bwa_clientid_1234567890."
        "bws_clientsecret_1234567890abcdef:"
        "enc_key_1234567890abcdefABCDEF=="
    )
    reference_id = "11111111-2222-3333-4444-555555555555"
    logins: list[str] = []
    client = _FakeClient(
        [
            {"id": reference_id, "key": helper.GMAIL_PRIMARY_SECRET_KEY},
        ],
        logins,
    )
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = list(argv)
        observed["input"] = kwargs.get("input")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    helper.bootstrap_gmail_primary_reference_index(
        _helper_authority(tmp_path, token),
        client_factory=lambda: client,
        encrypted_credential_root=tmp_path / "encrypted",
        run=fake_run,
    )

    assert logins == [token]
    assert client._secrets.calls == ["00000000-0000-0000-0000-000000000000"]
    assert observed["argv"] == [
        "systemd-creds",
        "encrypt",
        "--name=skeleton-secret-reference-index",
        "-",
        str(tmp_path / "encrypted" / "skeleton-secret-reference-index"),
    ]
    payload = json.loads(observed["input"])
    assert payload["registrations"][0]["reference_id"] == reference_id
    assert token.encode() not in observed["input"]


def test_bitwarden_helper_zero_or_ambiguous_gmail_metadata_fails_before_persistence(tmp_path: Path) -> None:
    token = (
        "0."
        "bwa_clientid_1234567890."
        "bws_clientsecret_1234567890abcdef:"
        "enc_key_1234567890abcdefABCDEF=="
    )
    for rows, reason in (
        ([], "gmail_primary_reference_not_found"),
        (
            [
                {"id": "11111111-2222-3333-4444-555555555555", "key": helper.GMAIL_PRIMARY_SECRET_KEY},
                {"id": "66666666-7777-8888-9999-000000000000", "key": helper.GMAIL_PRIMARY_SECRET_KEY},
            ],
            "gmail_primary_reference_ambiguous",
        ),
    ):
        with pytest.raises(helper.HelperError, match=reason):
            helper.bootstrap_gmail_primary_reference_index(
                _helper_authority(tmp_path / reason, token),
                client_factory=lambda rows=rows: _FakeClient(rows, []),
                encrypted_credential_root=tmp_path / "encrypted",
                run=lambda *_args, **_kwargs: pytest.fail("must fail before systemd-creds"),
            )


def test_bitwarden_sdk_helper_install_path_matches_activation_invocation() -> None:
    root = Path(__file__).resolve().parents[1]
    installer = (root / "scripts" / "install_bitwarden_sdk_runtime.sh").read_text(encoding="utf-8")
    mail_installer = (root / "scripts" / "install_mail_operations_worker.sh").read_text(encoding="utf-8")
    runner_source = (root / "scripts" / "runner_poll_github_tasks.py").read_text(encoding="utf-8")

    expected = "/opt/skeleton-bitwarden-sdk-runtime/bin/bitwarden-gmail-primary-reference-helper"
    assert expected in installer
    assert expected in runner_source
    assert "scripts/install_bitwarden_sdk_runtime.sh" in mail_installer
    assert "bitwarden-sdk==2.1.0" in installer
    assert "b6f6b2624e340307891edc20a2860ec0b7c2140683eec2ce2e20d1076ffe9268" in installer


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
