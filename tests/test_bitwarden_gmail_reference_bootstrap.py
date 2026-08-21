from __future__ import annotations

import base64
import json
import subprocess
from types import SimpleNamespace

import pytest

from scripts import bitwarden_gmail_reference_bootstrap as bootstrap


TOKEN = "0.client_id.machine_secret_value_123:encryption_key_value_456"
CLIENT_ID = "client_id"
CLIENT_SECRET = "machine_secret_value_123"
ENCRYPTION_KEY = "encryption_key_value_456"
ORG_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
REF_ID = "11111111-2222-3333-4444-555555555555"


def _jwt(**claims: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("ascii").rstrip("=")
    return f"header.{payload}.signature"


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_machine_token_parser_accepts_only_documented_shape() -> None:
    parsed = bootstrap.parse_machine_token(TOKEN)

    assert parsed.client_id == CLIENT_ID
    assert parsed.client_secret == CLIENT_SECRET
    assert parsed.encryption_key == ENCRYPTION_KEY


@pytest.mark.parametrize(
    "token",
    [
        "",
        "client_id.machine_secret_value_123:encryption_key_value_456",
        "1.client_id.machine_secret_value_123:encryption_key_value_456",
        "0.client_id.machine_secret_value_123",
        "0.client_id.machine_secret_value_123:",
        "0.client_id:machine_secret_value_123:encryption_key_value_456",
        " 0.client_id.machine_secret_value_123:encryption_key_value_456",
        "0.client_id.machine_secret_value_123:encryption_key_value_456\n",
    ],
)
def test_machine_token_parser_rejects_malformed_tokens(token: str) -> None:
    with pytest.raises(bootstrap.BootstrapError, match="machine_token_invalid"):
        bootstrap.parse_machine_token(token)


def test_identity_post_sends_secret_without_encryption_key() -> None:
    observed: dict[str, object] = {}

    def opener(request, timeout: int):
        observed["url"] = request.full_url
        observed["timeout"] = timeout
        observed["body"] = request.data.decode("ascii")
        return _Response({"access_token": _jwt(organization=ORG_ID)})

    jwt = bootstrap.request_identity_jwt(TOKEN, opener=opener)

    assert jwt == _jwt(organization=ORG_ID)
    assert observed["url"] == bootstrap.IDENTITY_TOKEN_URL
    assert f"client_id={CLIENT_ID}" in observed["body"]
    assert f"client_secret={CLIENT_SECRET}" in observed["body"]
    assert ENCRYPTION_KEY not in observed["body"]
    assert ":" not in str(observed["body"])


class _Secrets:
    def __init__(self, items: list[dict[str, str]]) -> None:
        self.items = items
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def list(self, organization_id: str):
        self.calls.append(("list", (organization_id,)))
        return SimpleNamespace(data=self.items)

    def get(self, *args):
        self.calls.append(("get", args))
        raise AssertionError("secret value retrieval is forbidden")

    def get_by_ids(self, *args):
        self.calls.append(("get_by_ids", args))
        raise AssertionError("secret value retrieval is forbidden")

    def sync(self, *args):
        self.calls.append(("sync", args))
        raise AssertionError("secret sync is forbidden")


class _SdkClient:
    def __init__(self, items: list[dict[str, str]]) -> None:
        self.secrets_api = _Secrets(items)
        self.login_tokens: list[str] = []

    def login_access_token(self, token: str) -> None:
        self.login_tokens.append(token)

    def secrets(self) -> _Secrets:
        return self.secrets_api


def _opener(_request, timeout: int):
    assert timeout == 30
    return _Response({"access_token": _jwt(organization=ORG_ID)})


def test_sdk_login_gets_complete_original_token_and_discovery_is_metadata_only() -> None:
    client = _SdkClient(
        [{"id": REF_ID, "key": bootstrap.CANONICAL_GMAIL_SECRET_KEY}]
    )

    reference = bootstrap.discover_canonical_gmail_reference(
        TOKEN,
        client=client,
        opener=_opener,
    )

    assert reference == REF_ID
    assert client.login_tokens == [TOKEN]
    assert client.secrets_api.calls == [("list", (ORG_ID,))]


def test_reference_match_fails_closed_for_none_and_ambiguous() -> None:
    with pytest.raises(bootstrap.BootstrapError, match="gmail_reference_not_found"):
        bootstrap.discover_canonical_gmail_reference(
            TOKEN,
            client=_SdkClient([]),
            opener=_opener,
        )

    with pytest.raises(bootstrap.BootstrapError, match="gmail_reference_ambiguous"):
        bootstrap.discover_canonical_gmail_reference(
            TOKEN,
            client=_SdkClient(
                [
                    {"id": REF_ID, "key": bootstrap.CANONICAL_GMAIL_SECRET_KEY},
                    {
                        "id": "66666666-7777-8888-9999-000000000000",
                        "key": bootstrap.CANONICAL_GMAIL_SECRET_KEY,
                    },
                ]
            ),
            opener=_opener,
        )


def test_systemd_creds_encrypt_receives_reference_index_on_stdin_only() -> None:
    observed: dict[str, object] = {}

    def runner(argv, **kwargs):
        observed["argv"] = argv
        observed["input"] = kwargs["input"]
        return subprocess.CompletedProcess(argv, 0, "", "")

    bootstrap.encrypt_reference_index(REF_ID, output_path="/tmp/encrypted-index", runner=runner)

    assert observed["argv"] == [
        "systemd-creds",
        "encrypt",
        "--name",
        bootstrap.REFERENCE_INDEX_CREDENTIAL_NAME,
        "-",
        "/tmp/encrypted-index",
    ]
    assert REF_ID not in " ".join(observed["argv"])
    payload = json.loads(str(observed["input"]))
    assert payload["registrations"][0]["reference_id"] == REF_ID


def test_public_receipt_contains_no_uuid_or_token_pieces(monkeypatch) -> None:
    monkeypatch.setattr(
        bootstrap,
        "discover_canonical_gmail_reference",
        lambda _token: REF_ID,
    )
    monkeypatch.setattr(bootstrap, "encrypt_reference_index", lambda _ref: None)

    receipt = bootstrap.bootstrap(TOKEN)
    serialized = json.dumps(receipt, sort_keys=True)

    assert REF_ID not in serialized
    for piece in (CLIENT_ID, CLIENT_SECRET, ENCRYPTION_KEY):
        assert piece not in serialized
    assert receipt["plaintext_index_file"] is False
