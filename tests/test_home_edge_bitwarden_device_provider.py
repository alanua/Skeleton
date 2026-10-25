import json
import subprocess
import pytest
from core.home_edge.bitwarden_device_provider import BitwardenSecretsProvider
from core.secret_store import SecretReference, SecretResolutionContext, SecretProviderUnavailable

ID = "5acff9bc-1292-42a5-b961-b4ee00cd824c"
ctx = SecretResolutionContext(machine_identity="home-edge-01",audience="brother_printer",task_kind="print_head_clean_black_normal")
ref = SecretReference(provider="bitwarden",reference_id=ID)


def test_fetches_one_exact_secret_without_leaking_to_representations(monkeypatch):
    seen=[]
    def fake_run(argv,**kw):
        seen.append((argv,kw))
        return subprocess.CompletedProcess(argv,0,json.dumps({"id":ID,"value":"do-not-log"}),"")
    monkeypatch.setattr(subprocess,"run",fake_run)
    secret=BitwardenSecretsProvider(token_reader=lambda:"private-token").resolve(ref,ctx)
    assert str(secret)=="<redacted>"
    assert "do-not-log" not in repr(secret)
    assert seen[0][0][2]==ID
    assert "private-token" not in str(seen[0][0])
    assert seen[0][1]["env"]["BWS_ACCESS_TOKEN"]=="private-token"


def test_scope_and_reference_fail_closed_without_fetch(monkeypatch):
    def fail(*a,**kw):
        pytest.fail("unexpected vault fetch")
    monkeypatch.setattr(subprocess,"run",fail)
    provider=BitwardenSecretsProvider(token_reader=lambda:"secret-token")
    with pytest.raises(SecretProviderUnavailable):
        provider.resolve(SecretReference(provider="bitwarden",reference_id="unrecognized"),ctx)
    with pytest.raises(SecretProviderUnavailable):
        provider.resolve(ref,SecretResolutionContext(machine_identity="other-node",audience="brother_printer",task_kind="clean"))


def test_missing_vault_token_fails_closed(monkeypatch):
    monkeypatch.setattr(subprocess,"run",lambda *a,**kw:pytest.fail("unexpected vault fetch"))
    with pytest.raises(SecretProviderUnavailable):
        BitwardenSecretsProvider(token_reader=lambda:"").resolve(ref,ctx)


def test_bws_error_is_redacted(monkeypatch):
    monkeypatch.setattr(subprocess,"run",lambda a,**kw:subprocess.CompletedProcess(a,1,"","leaked-password"))
    with pytest.raises(SecretProviderUnavailable) as exc:
        BitwardenSecretsProvider(token_reader=lambda:"token").resolve(ref,ctx)
    assert "leaked-password" not in str(exc.value)


def test_bws_response_id_mismatch_is_rejected(monkeypatch):
    monkeypatch.setattr(subprocess,"run",lambda a,**kw:subprocess.CompletedProcess(a,0,json.dumps({"id":"different","value":"private"}),""))
    with pytest.raises(SecretProviderUnavailable):
        BitwardenSecretsProvider(token_reader=lambda:"token").resolve(ref,ctx)
