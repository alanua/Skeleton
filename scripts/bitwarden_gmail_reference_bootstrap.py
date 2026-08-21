#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


BITWARDEN_SDK_VERSION = "2.1.0"
REFERENCE_INDEX_SCHEMA = "skeleton.secret_reference_index.v1"
SERVICE_ID = "mail-gmail"
ALIAS = "acct:gmail-primary"
GMAIL_PRIMARY_IDENTIFIER_RE = re.compile(
    r"^skeleton/mail-gmail/acct:gmail-primary/oauth-readonly$"
)
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class BootstrapError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _read_access_token(path: Path) -> str:
    if not path.is_absolute() or not path.is_file():
        raise BootstrapError("ACCESS_TOKEN_CREDENTIAL_UNAVAILABLE")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise BootstrapError("ACCESS_TOKEN_CREDENTIAL_UNAVAILABLE") from exc
    if not value:
        raise BootstrapError("ACCESS_TOKEN_CREDENTIAL_UNAVAILABLE")
    return value


def _object_field(item: object, name: str) -> object:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _response_items(response: object) -> Iterable[object]:
    data = _object_field(response, "data")
    if isinstance(data, list):
        return data
    nested = _object_field(data, "data")
    if isinstance(nested, list):
        return nested
    if isinstance(response, list):
        return response
    raise BootstrapError("SECRET_IDENTIFIER_CONTRACT_MISMATCH")


def _secret_key(item: object) -> str | None:
    value = _object_field(item, "key")
    return value if isinstance(value, str) else None


def _secret_id(item: object) -> str | None:
    value = _object_field(item, "id")
    if isinstance(value, str) and UUID_RE.fullmatch(value):
        return value.lower()
    return None


def _org_id(value: object) -> str:
    if not isinstance(value, str) or not UUID_RE.fullmatch(value):
        raise BootstrapError("ACCESS_TOKEN_ORGANIZATION_UNAVAILABLE")
    return value.lower()


def discover_gmail_primary_reference(*, token_file: Path, state_file: Path | None) -> tuple[str, int]:
    access_token = _read_access_token(token_file)
    try:
        import bitwarden_sdk
        from bitwarden_sdk import BitwardenClient, DeviceType, client_settings_from_dict
    except Exception as exc:  # pragma: no cover - exercised by subprocess contract tests.
        raise BootstrapError("BITWARDEN_SDK_UNAVAILABLE") from exc
    if getattr(bitwarden_sdk, "__version__", None) != BITWARDEN_SDK_VERSION:
        raise BootstrapError("BITWARDEN_SDK_VERSION_MISMATCH")

    api_url = os.getenv("BITWARDEN_API_URL", "https://api.bitwarden.com")
    identity_url = os.getenv("BITWARDEN_IDENTITY_URL", "https://identity.bitwarden.com")
    client = BitwardenClient(
        client_settings_from_dict(
            {
                "apiUrl": api_url,
                "deviceType": DeviceType.SDK,
                "identityUrl": identity_url,
                "userAgent": "Skeleton Bitwarden metadata bootstrap",
            }
        )
    )

    auth = client.auth()
    auth.login_access_token(access_token, str(state_file) if state_file is not None else None)
    organization_id = _org_id(auth.get_access_token_organization())
    identifiers = client.secrets().list(organization_id)

    matches: list[str] = []
    for item in _response_items(identifiers):
        if _secret_key(item) == GMAIL_PRIMARY_IDENTIFIER_RE.pattern.strip("^$"):
            secret_id = _secret_id(item)
            if secret_id is None:
                raise BootstrapError("SECRET_IDENTIFIER_CONTRACT_MISMATCH")
            matches.append(secret_id)

    if len(matches) == 0:
        raise BootstrapError("REFERENCE_MATCH_NONE")
    if len(matches) != 1:
        raise BootstrapError("REFERENCE_MATCH_AMBIGUOUS")
    return matches[0], len(matches)


def _index_payload(reference_id: str) -> dict[str, object]:
    return {
        "schema": REFERENCE_INDEX_SCHEMA,
        "registrations": [
            {
                "service_id": SERVICE_ID,
                "alias": ALIAS,
                "provider": "bitwarden",
                "reference_id": reference_id,
            }
        ],
    }


def _write_atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    if not path.is_absolute():
        raise BootstrapError("OUTPUT_INDEX_PATH_INVALID")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _public_receipt(
    *,
    status: str,
    match_count: int,
    persisted: bool,
    reason: str,
) -> dict[str, object]:
    return {
        "schema": "skeleton.bitwarden_reference_bootstrap_receipt.v1",
        "status": status,
        "match_count": match_count,
        "persisted": persisted,
        "reason": reason,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--output-index", required=True)
    parser.add_argument("--state-file")
    args = parser.parse_args(argv)

    try:
        reference_id, match_count = discover_gmail_primary_reference(
            token_file=Path(args.token_file),
            state_file=Path(args.state_file) if args.state_file else None,
        )
        _write_atomic_json(Path(args.output_index), _index_payload(reference_id))
    except BootstrapError as exc:
        print(
            json.dumps(
                _public_receipt(
                    status="BLOCKED",
                    match_count=0 if exc.reason == "REFERENCE_MATCH_NONE" else 2 if exc.reason == "REFERENCE_MATCH_AMBIGUOUS" else 0,
                    persisted=False,
                    reason=exc.reason,
                ),
                sort_keys=True,
            )
        )
        return 1
    except Exception:
        print(
            json.dumps(
                _public_receipt(
                    status="BLOCKED",
                    match_count=0,
                    persisted=False,
                    reason="BITWARDEN_BOOTSTRAP_UNEXPECTED_FAILURE",
                ),
                sort_keys=True,
            )
        )
        return 1

    print(
        json.dumps(
            _public_receipt(
                status="DONE",
                match_count=match_count,
                persisted=True,
                reason="OK",
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
