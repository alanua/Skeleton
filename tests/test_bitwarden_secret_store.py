from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
import types

import pytest

import integrations.bitwarden_secret_store as bitwarden
import scripts.bitwarden_secret_identifier_helper as identifier_helper
from core.secret_store import SecretProviderUnavailable, SecretReference, SecretResolutionContext
from integrations.bitwarden_secret_store import (
    BwsCliSecretsManagerStore,
    bootstrap_registered_bitwarden_reference_index,
    bitwarden_reference_from_systemd_credential,
)


def _authority(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    token_file = credentials / "bitwarden-access-token"
    token_file.write_text("synthetic-machine-token\n", encoding="utf-8")
    ref_file = credentials / "openrouter-secret-ref"
    ref_file.write_text("11111111-2222-3333-4444-555555555555\n", encoding="utf-8")
    (credentials / "bitwarden-organization-id").write_text(
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n",
        encoding="utf-8",
    )
    (credentials / "skeleton-secret-reference-index").write_text(
        json.dumps({"schema": "skeleton.secret_reference_index.v1", "registrations": []}),
        encoding="utf-8",
    )
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
        observed.setdefault("argvs", []).append(list(argv))
        if argv[:3] == ["sudo", "-n", "systemd-creds"]:
            observed["encrypted_input"] = kwargs["input"]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
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


def test_identifier_bootstrap_uses_fixed_isolated_helper_without_token_in_argv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    authority, _bws, _ref_file = _authority(tmp_path)
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed.setdefault("argvs", []).append(list(argv))
        if argv[:3] == ["sudo", "-n", "systemd-creds"]:
            observed["encrypted_input"] = kwargs["input"]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        observed["argv"] = list(argv)
        observed["env"] = dict(kwargs["env"])
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "DONE",
                    "reason": "OK",
                    "match_status": "ONE",
                    "matched_key": "gmail-primary-oauth",
                    "reference_id": "11111111-2222-3333-4444-555555555555",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(bitwarden.subprocess, "run", fake_run)
    receipt = bootstrap_registered_bitwarden_reference_index(
        authority,
        service_id="mail-gmail",
        alias="acct:gmail-primary",
    )

    assert receipt == {"status": "DONE", "reason": "OK", "match_status": "ONE"}
    assert observed["argv"] == [
        "/opt/skeleton-bitwarden-sdk/bin/python",
        "/opt/skeleton-bitwarden-sdk/bitwarden_secret_identifier_helper.py",
    ]
    assert observed["argvs"][1] == [
        "sudo",
        "-n",
        "systemd-creds",
        "encrypt",
        "--name=skeleton-secret-reference-index",
        "-",
        "/etc/credstore.encrypted/skeleton-secret-reference-index",
    ]
    assert "synthetic-machine-token" not in " ".join(observed["argv"])
    assert "synthetic-machine-token" not in " ".join(observed["argvs"][1])
    assert observed["env"]["BWS_ACCESS_TOKEN"] == "synthetic-machine-token"
    assert observed["env"]["BWS_ORGANIZATION_ID"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert observed["env"]["SKELETON_BITWARDEN_SERVICE_ID"] == "mail-gmail"
    assert observed["env"]["SKELETON_BITWARDEN_ALIAS"] == "acct:gmail-primary"
    index = json.loads(
        (Path(authority["CREDENTIALS_DIRECTORY"]) / "skeleton-secret-reference-index").read_text(
            encoding="utf-8",
        )
    )
    assert index["registrations"] == [
        {
            "service_id": "mail-gmail",
            "alias": "acct:gmail-primary",
            "provider": "bitwarden",
            "reference_id": "11111111-2222-3333-4444-555555555555",
        }
    ]
    assert json.loads(observed["encrypted_input"]) == index


@pytest.mark.parametrize(
    ("match_status", "reason"),
    [("ZERO", "IDENTIFIER_MATCH_ZERO"), ("MANY", "IDENTIFIER_MATCH_AMBIGUOUS")],
)
def test_identifier_bootstrap_fails_closed_without_persisting_zero_or_many(
    tmp_path: Path,
    monkeypatch,
    match_status: str,
    reason: str,
) -> None:
    authority, _bws, _ref_file = _authority(tmp_path)

    monkeypatch.setattr(
        bitwarden.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": reason,
                    "match_status": match_status,
                    "reference_id": "11111111-2222-3333-4444-555555555555",
                }
            ),
            stderr="",
        ),
    )

    receipt = bootstrap_registered_bitwarden_reference_index(
        authority,
        service_id="mail-gmail",
        alias="acct:gmail-primary",
    )

    assert receipt["status"] == "BLOCKED"
    assert receipt["match_status"] == match_status
    index = json.loads(
        (Path(authority["CREDENTIALS_DIRECTORY"]) / "skeleton-secret-reference-index").read_text(
            encoding="utf-8",
        )
    )
    assert index["registrations"] == []


def test_identifier_bootstrap_rejects_unregistered_matcher_before_helper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    authority, _bws, _ref_file = _authority(tmp_path)
    helper_calls: list[bool] = []
    monkeypatch.setattr(
        bitwarden.subprocess,
        "run",
        lambda *_args, **_kwargs: helper_calls.append(True),
    )

    receipt = bootstrap_registered_bitwarden_reference_index(
        authority,
        service_id="mail-gmail",
        alias="acct:caller-selected",
    )

    assert receipt == {
        "status": "BLOCKED",
        "reason": "REGISTERED_IDENTIFIER_MATCHER_UNAVAILABLE",
    }
    assert helper_calls == []


def _install_fake_sdk(monkeypatch, *, data):
    calls: list[str] = []

    class FakeAuth:
        def login_access_token(self, token):
            calls.append(f"login:{token}")

    class FakeSecrets:
        def list(self, organization_id):
            calls.append(f"list:{organization_id}")
            return SimpleNamespace(data=data)

        def get(self, _secret_id):
            raise AssertionError("value-bearing get must not be called")

        def get_by_ids(self, _secret_ids):
            raise AssertionError("value-bearing get_by_ids must not be called")

        def sync(self, _organization_id):
            raise AssertionError("value-bearing sync must not be called")

    class FakeClient:
        def __init__(self, settings):
            calls.append(f"client:{settings['userAgent']}")

        def auth(self):
            return FakeAuth()

        def secrets(self):
            return FakeSecrets()

    fake_module = types.ModuleType("bitwarden_sdk")
    fake_module.BitwardenClient = FakeClient
    fake_module.DeviceType = SimpleNamespace(SDK="SDK")
    fake_module.client_settings_from_dict = lambda settings: settings
    monkeypatch.setitem(sys.modules, "bitwarden_sdk", fake_module)
    monkeypatch.setattr(
        identifier_helper.importlib.metadata,
        "version",
        lambda package: "2.1.0" if package == "bitwarden-sdk" else "0",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["bitwarden_secret_identifier_helper.py"],
    )
    monkeypatch.setenv("BWS_ACCESS_TOKEN", "TOKEN_SENTINEL")
    monkeypatch.setenv("BWS_ORGANIZATION_ID", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    monkeypatch.setenv(
        "SKELETON_BITWARDEN_EXPECTED_KEYS_JSON",
        json.dumps(["gmail-primary-oauth"]),
    )
    return calls


def test_identifier_helper_uses_pinned_list_surface_and_emits_only_identifier_metadata(
    monkeypatch,
    capsys,
) -> None:
    calls = _install_fake_sdk(
        monkeypatch,
        data=[
            {
                "id": "11111111-2222-3333-4444-555555555555",
                "organization_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "key": "gmail-primary-oauth",
                "project_ids": ["99999999-2222-3333-4444-555555555555"],
            }
        ],
    )

    assert identifier_helper.main() == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "status": "DONE",
        "reason": "OK",
        "match_status": "ONE",
        "matched_key": "gmail-primary-oauth",
        "reference_id": "11111111-2222-3333-4444-555555555555",
    }
    assert calls == [
        "client:SkeletonBitwardenIdentifierHelper/1",
        "login:TOKEN_SENTINEL",
        "list:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    ]
    assert "TOKEN_SENTINEL" not in capsys.readouterr().err


def test_identifier_helper_rejects_value_bearing_identifier_fields(
    monkeypatch,
    capsys,
) -> None:
    _install_fake_sdk(
        monkeypatch,
        data=[
            {
                "id": "11111111-2222-3333-4444-555555555555",
                "organization_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "key": "gmail-primary-oauth",
                "value": "SECRET_VALUE_SENTINEL",
            }
        ],
    )

    assert identifier_helper.main() == 1
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert payload["status"] == "BLOCKED"
    assert payload["reason"] == "VALUE_BEARING_IDENTIFIER_FIELD"
    assert "SECRET_VALUE_SENTINEL" not in output


def test_identifier_helper_rejects_unpinned_sdk_version(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        identifier_helper.importlib.metadata,
        "version",
        lambda package: "2.0.0" if package == "bitwarden-sdk" else "0",
    )
    monkeypatch.setattr(sys, "argv", ["bitwarden_secret_identifier_helper.py"])

    assert identifier_helper.main() == 1

    assert json.loads(capsys.readouterr().out)["reason"] == "SDK_VERSION_MISMATCH"


def test_bitwarden_sdk_runtime_installer_is_pinned_and_isolated() -> None:
    script = Path("scripts/install_bitwarden_sdk_runtime.sh").read_text(encoding="utf-8")

    assert 'runtime_root="/opt/skeleton-bitwarden-sdk"' in script
    assert '"${python_bin}" -m pip install' in script
    assert "bitwarden-sdk==2.1.0" in script
    assert "--only-binary=:all:" in script
    assert "pip install --upgrade" not in script
    assert "python3 -m pip install" not in script


def test_mail_service_uses_systemd_encrypted_credential_boundary() -> None:
    unit = Path("ops/systemd/skeleton-mail-operations.service").read_text(
        encoding="utf-8",
    )

    assert (
        "LoadCredentialEncrypted=bitwarden-access-token:"
        "/etc/credstore.encrypted/bitwarden-access-token"
    ) in unit
    assert (
        "LoadCredentialEncrypted=skeleton-secret-reference-index:"
        "/etc/credstore.encrypted/skeleton-secret-reference-index"
    ) in unit
    assert "Environment=BWS_ACCESS_TOKEN" not in unit
    assert "EnvironmentFile" not in unit
