from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

import integrations.bitwarden_secret_store as bitwarden
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


def _install_synthetic_bitwarden_sdk(tmp_path: Path) -> Path:
    package = tmp_path / "runtime" / "bitwarden_sdk"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        textwrap.dedent(
            """
            import json
            import os

            __version__ = "2.1.0"

            class DeviceType:
                SDK = "SDK"

            def client_settings_from_dict(value):
                return dict(value)

            class _Response:
                def __init__(self, data):
                    self.data = data

            class _Auth:
                def __init__(self):
                    self.login_calls = []

                def login_access_token(self, token, state_file):
                    if token != "synthetic-machine-token":
                        raise RuntimeError("bad token")
                    self.login_calls.append((token, state_file))

                def get_access_token_organization(self):
                    return "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

            class _Secrets:
                def list(self, organization_id):
                    if organization_id != "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee":
                        raise RuntimeError("bad organization")
                    return _Response(json.loads(os.environ["BITWARDEN_SYNTHETIC_IDENTIFIERS_JSON"]))

                def get(self, *args, **kwargs):
                    raise AssertionError("secret get must not be called")

                def get_by_ids(self, *args, **kwargs):
                    raise AssertionError("secret get_by_ids must not be called")

            class BitwardenClient:
                def __init__(self, settings):
                    self.settings = settings
                    self._auth = _Auth()
                    self._secrets = _Secrets()

                def auth(self):
                    return self._auth

                def secrets(self):
                    return self._secrets
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return package.parent


def _run_bootstrap_helper(
    tmp_path: Path,
    identifiers: list[dict[str, str]],
) -> tuple[subprocess.CompletedProcess[str], Path]:
    runtime = _install_synthetic_bitwarden_sdk(tmp_path)
    token_file = tmp_path / "bitwarden-access-token"
    token_file.write_text("synthetic-machine-token\n", encoding="utf-8")
    output_index = tmp_path / "out" / "skeleton-secret-reference-index"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(runtime)
    env["BITWARDEN_SYNTHETIC_IDENTIFIERS_JSON"] = json.dumps(identifiers)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/bitwarden_gmail_reference_bootstrap.py",
            "--token-file",
            str(token_file),
            "--output-index",
            str(output_index),
            "--state-file",
            str(tmp_path / "state.json"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )
    return result, output_index


def test_bitwarden_bootstrap_helper_persists_only_unique_gmail_primary_uuid(tmp_path: Path) -> None:
    secret_id = "11111111-2222-3333-4444-555555555555"
    result, output_index = _run_bootstrap_helper(
        tmp_path,
        [
            {"id": secret_id, "key": "skeleton/mail-gmail/acct:gmail-primary/oauth-readonly"},
            {"id": "66666666-7777-8888-9999-000000000000", "key": "other"},
        ],
    )

    assert result.returncode == 0
    receipt = json.loads(result.stdout)
    assert receipt == {
        "schema": "skeleton.bitwarden_reference_bootstrap_receipt.v1",
        "status": "DONE",
        "match_count": 1,
        "persisted": True,
        "reason": "OK",
    }
    payload = json.loads(output_index.read_text(encoding="utf-8"))
    assert payload == {
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
    assert secret_id not in result.stdout
    assert "synthetic-machine-token" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("identifiers", "reason"),
    [
        ([], "REFERENCE_MATCH_NONE"),
        (
            [
                {"id": "11111111-2222-3333-4444-555555555555", "key": "skeleton/mail-gmail/acct:gmail-primary/oauth-readonly"},
                {"id": "66666666-7777-8888-9999-000000000000", "key": "skeleton/mail-gmail/acct:gmail-primary/oauth-readonly"},
            ],
            "REFERENCE_MATCH_AMBIGUOUS",
        ),
    ],
)
def test_bitwarden_bootstrap_helper_fails_closed_on_none_or_ambiguous(
    tmp_path: Path,
    identifiers: list[dict[str, str]],
    reason: str,
) -> None:
    result, output_index = _run_bootstrap_helper(tmp_path, identifiers)

    assert result.returncode == 1
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "BLOCKED"
    assert receipt["persisted"] is False
    assert receipt["reason"] == reason
    assert not output_index.exists()


def test_bitwarden_bootstrap_helper_has_no_value_bearing_discovery_calls() -> None:
    source = Path("scripts/bitwarden_gmail_reference_bootstrap.py").read_text(encoding="utf-8")
    forbidden = (
        ".secrets().get(",
        ".secrets().get_by_ids(",
        ".get_by_ids(",
        " secret list",
        "bws secret list",
        "export",
        "run ",
    )
    for needle in forbidden:
        assert needle not in source
