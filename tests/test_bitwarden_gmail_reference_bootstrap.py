from __future__ import annotations

import base64
import json
from types import SimpleNamespace
import sys

import pytest

from scripts import bitwarden_gmail_reference_bootstrap as bootstrap


CLIENT_ID = "organization.11111111-2222-3333-4444-555555555555"
CLIENT_SECRET = "synthetic-client-secret"
MACHINE_TOKEN = f"0.{CLIENT_ID}.{CLIENT_SECRET}"
ORG_ID = "11111111-2222-4333-8444-555555555555"
SECRET_ID = "22222222-3333-4444-8555-666666666666"


def _jwt(payload: dict[str, object]) -> str:
    def encode(value: object) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return ".".join((encode({"alg": "synthetic"}), encode(payload), "signature"))


def _token_response(**payload_updates: object) -> dict[str, object]:
    payload = {
        "iss": "https://identity.bitwarden.com",
        "exp": 9999999999,
        "scope": "api.secrets",
        "organization": ORG_ID,
        "client_id": CLIENT_ID,
    }
    payload.update(payload_updates)
    return {
        "token_type": "Bearer",
        "scope": "api.secrets",
        "access_token": _jwt(payload),
    }


def test_synthetic_identity_protocol_posts_exact_form_fields(monkeypatch) -> None:
    observed: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps(_token_response()).encode("utf-8")

    def fake_urlopen(request, *, timeout):
        observed["url"] = request.full_url
        observed["method"] = request.get_method()
        observed["headers"] = dict(request.header_items())
        observed["body"] = request.data.decode("ascii")
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setattr(bootstrap, "urlopen", fake_urlopen)

    response = bootstrap.request_identity_token(
        identity_url=bootstrap.IDENTITY_TOKEN_URL,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )

    assert response["token_type"] == "Bearer"
    assert observed["url"] == "https://identity.bitwarden.com/connect/token"
    assert observed["method"] == "POST"
    assert observed["headers"]["Content-type"] == "application/x-www-form-urlencoded"
    assert observed["body"] == (
        "grant_type=client_credentials&scope=api.secrets&"
        f"client_id={CLIENT_ID}&client_secret={CLIENT_SECRET}"
    )


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ({"token_type": "Bearer", "scope": "api.secrets", "access_token": "bad"}, "IDENTITY_JWT_MALFORMED"),
        (_token_response(iss="https://vault.bitwarden.com"), "IDENTITY_TOKEN_ISSUER_MISMATCH"),
        (_token_response(organization="not-a-uuid"), "IDENTITY_TOKEN_ORGANIZATION_INVALID"),
        (_token_response(exp=999), "IDENTITY_TOKEN_EXPIRED"),
        (
            {
                **_token_response(),
                "scope": "api.organization",
            },
            "IDENTITY_TOKEN_SCOPE_MISMATCH",
        ),
        (_token_response(client_id="other-client"), "IDENTITY_TOKEN_CLIENT_ID_MISMATCH"),
    ],
)
def test_jwt_payload_validation_rejects_bad_identity_claims(response, reason) -> None:
    with pytest.raises(bootstrap.BootstrapError, match=reason):
        bootstrap.validate_identity_token_response(
            response,
            identity_url=bootstrap.IDENTITY_TOKEN_URL,
            client_id=CLIENT_ID,
            now=1000,
        )


def test_jwt_payload_validation_accepts_expected_identity_claims() -> None:
    assert (
        bootstrap.validate_identity_token_response(
            _token_response(),
            identity_url=bootstrap.IDENTITY_TOKEN_URL,
            client_id=CLIENT_ID,
            now=1000,
        )
        == ORG_ID
    )


def test_sdk_discovery_calls_only_login_access_token_and_secrets_list(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class Secrets:
        def list(self, organization_id):
            calls.append(("secrets.list", organization_id))
            return [{"id": SECRET_ID, "key": bootstrap.GMAIL_SECRET_KEY}]

        def get(self, *_args, **_kwargs):
            raise AssertionError("secret get must not be called")

        def get_by_ids(self, *_args, **_kwargs):
            raise AssertionError("secret get_by_ids must not be called")

        def sync(self, *_args, **_kwargs):
            raise AssertionError("secret sync must not be called")

    class Client:
        def login_access_token(self, token):
            calls.append(("login_access_token", token))

        def secrets(self):
            calls.append(("secrets", None))
            return Secrets()

    monkeypatch.setitem(sys.modules, "bitwarden_sdk", SimpleNamespace(BitwardenClient=Client))

    reference, match_count_class = bootstrap.discover_gmail_secret_reference(
        machine_token=MACHINE_TOKEN,
        organization_id=ORG_ID,
    )

    assert reference == SECRET_ID
    assert match_count_class == "one"
    assert calls == [
        ("login_access_token", MACHINE_TOKEN),
        ("secrets", None),
        ("secrets.list", ORG_ID),
    ]


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([{"id": SECRET_ID, "key": "other"}], "SECRET_MATCH_NONE"),
        (
            [
                {"id": SECRET_ID, "key": bootstrap.GMAIL_SECRET_KEY},
                {
                    "id": "33333333-4444-4555-8666-777777777777",
                    "key": bootstrap.GMAIL_SECRET_KEY,
                },
            ],
            "SECRET_MATCH_AMBIGUOUS",
        ),
    ],
)
def test_sdk_discovery_rejects_none_and_ambiguous_matches(monkeypatch, rows, reason) -> None:
    class Secrets:
        def list(self, _organization_id):
            return rows

    class Client:
        def login_access_token(self, _token):
            return None

        def secrets(self):
            return Secrets()

    monkeypatch.setitem(sys.modules, "bitwarden_sdk", SimpleNamespace(BitwardenClient=Client))

    with pytest.raises(bootstrap.BootstrapError, match=reason):
        bootstrap.discover_gmail_secret_reference(
            machine_token=MACHINE_TOKEN,
            organization_id=ORG_ID,
        )


def test_exact_match_is_encrypted_from_stdin_without_plaintext_file(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["input"] = kwargs["input"]
        observed["stdout"] = kwargs["stdout"]
        observed["stderr"] = kwargs["stderr"]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    output = tmp_path / "encrypted-index"

    bootstrap.persist_reference_index(SECRET_ID, output_path=str(output))

    command = observed["command"]
    assert command[:4] == ["systemd-creds", "encrypt", "--name", "skeleton-secret-reference-index"]
    assert command[-2:] == ["-", str(output)]
    assert not output.exists()
    payload = json.loads(observed["input"])
    assert payload["registrations"][0]["reference_id"] == SECRET_ID


def test_public_main_receipt_contains_no_uuid_or_secret(monkeypatch, tmp_path, capsys) -> None:
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "bitwarden-access-token").write_text(MACHINE_TOKEN, encoding="utf-8")

    monkeypatch.setattr(
        bootstrap,
        "request_identity_token",
        lambda **_kwargs: _token_response(),
    )
    monkeypatch.setattr(
        bootstrap,
        "discover_gmail_secret_reference",
        lambda **_kwargs: (SECRET_ID, "one"),
    )
    monkeypatch.setattr(bootstrap, "persist_reference_index", lambda *_args, **_kwargs: None)

    assert bootstrap.main(["--credentials-directory", str(credentials)]) == 0
    output = capsys.readouterr().out
    receipt = json.loads(output)

    assert receipt == {
        "status": "DONE",
        "match_count_class": "one",
        "persisted": True,
        "reason": "OK",
    }
    assert SECRET_ID not in output
    assert ORG_ID not in output
    assert CLIENT_SECRET not in output
