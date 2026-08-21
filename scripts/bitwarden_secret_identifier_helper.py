#!/usr/bin/env python3
from __future__ import annotations

from collections.abc import Iterable, Mapping
import importlib.metadata
import json
import os
import re
import sys
from typing import Any


PINNED_SDK_VERSION = "2.1.0"
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
VALUE_BEARING_FIELDS = frozenset(
    {
        "value",
        "note",
        "notes",
        "secret",
        "secrets",
        "password",
        "access_token",
        "accessToken",
    }
)


def _public(status: str, reason: str, **fields: object) -> int:
    payload = {"status": status, "reason": reason, **fields}
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if status == "DONE" or reason.startswith("IDENTIFIER_MATCH_") else 1


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name}_MISSING")
    return value


def _load_expected_keys() -> tuple[str, ...]:
    raw = _required_env("SKELETON_BITWARDEN_EXPECTED_KEYS_JSON")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("EXPECTED_KEYS_INVALID") from exc
    if (
        not isinstance(decoded, list)
        or not decoded
        or len(decoded) > 8
        or any(not isinstance(item, str) or not item.strip() for item in decoded)
    ):
        raise RuntimeError("EXPECTED_KEYS_INVALID")
    return tuple(item.strip() for item in decoded)


def _validate_identifier(item: Any, organization_id: str) -> dict[str, str]:
    if not isinstance(item, Mapping):
        raise RuntimeError("IDENTIFIER_CONTRACT_MISMATCH")
    if set(item) & VALUE_BEARING_FIELDS:
        raise RuntimeError("VALUE_BEARING_IDENTIFIER_FIELD")
    identifier = item.get("id")
    org = item.get("organization_id", item.get("organizationId"))
    key = item.get("key")
    if not isinstance(identifier, str) or UUID_RE.fullmatch(identifier) is None:
        raise RuntimeError("IDENTIFIER_CONTRACT_MISMATCH")
    if not isinstance(org, str) or org.lower() != organization_id.lower():
        raise RuntimeError("IDENTIFIER_CONTRACT_MISMATCH")
    if not isinstance(key, str) or not key.strip():
        raise RuntimeError("IDENTIFIER_CONTRACT_MISMATCH")
    return {"id": identifier.lower(), "organization_id": org.lower(), "key": key}


def _mapping_from_object(item: Any) -> Mapping[str, Any]:
    if isinstance(item, Mapping):
        return item
    fields = {
        "id": getattr(item, "id", None),
        "organization_id": getattr(item, "organization_id", None),
        "organizationId": getattr(item, "organizationId", None),
        "key": getattr(item, "key", None),
    }
    for value_field in VALUE_BEARING_FIELDS:
        if hasattr(item, value_field):
            fields[value_field] = getattr(item, value_field)
    return fields


def _response_data(response: Any) -> Iterable[Any]:
    if isinstance(response, Mapping):
        data = response.get("data")
    else:
        data = getattr(response, "data", None)
    if data is None:
        raise RuntimeError("IDENTIFIER_CONTRACT_MISMATCH")
    if not isinstance(data, Iterable) or isinstance(data, (str, bytes)):
        raise RuntimeError("IDENTIFIER_CONTRACT_MISMATCH")
    return data


def _sdk_client():
    try:
        from bitwarden_sdk import BitwardenClient, DeviceType, client_settings_from_dict
    except Exception as exc:
        raise RuntimeError("SDK_IMPORT_FAILED") from exc
    api_url = os.environ.get("BW_API_URL", "https://api.bitwarden.com").strip()
    identity_url = os.environ.get(
        "BW_IDENTITY_URL",
        "https://identity.bitwarden.com",
    ).strip()
    settings = client_settings_from_dict(
        {
            "apiUrl": api_url,
            "identityUrl": identity_url,
            "deviceType": DeviceType.SDK,
            "userAgent": "SkeletonBitwardenIdentifierHelper/1",
        }
    )
    return BitwardenClient(settings)


def _login(client: Any, access_token: str) -> None:
    auth = getattr(client, "auth", None)
    if not callable(auth):
        raise RuntimeError("SDK_AUTH_SURFACE_UNSUPPORTED")
    auth_client = auth()
    login = getattr(auth_client, "login_access_token", None)
    if not callable(login):
        raise RuntimeError("SDK_AUTH_SURFACE_UNSUPPORTED")
    login(access_token)


def _list_identifiers(client: Any, organization_id: str) -> Any:
    secrets = getattr(client, "secrets", None)
    if not callable(secrets):
        raise RuntimeError("SDK_IDENTIFIER_SURFACE_UNSUPPORTED")
    secret_client = secrets()
    forbidden = ("get", "get_by_ids", "getByIds", "sync", "sync_with_values", "run", "export")
    if any(not callable(getattr(secret_client, name, None)) for name in ("list",)):
        raise RuntimeError("SDK_IDENTIFIER_SURFACE_UNSUPPORTED")
    if any(name in os.environ for name in ("BWS_SECRET_ID", "BWS_PROJECT_ID")):
        raise RuntimeError("CALLER_SECRET_SELECTOR_UNSUPPORTED")
    list_identifiers = getattr(secret_client, "list")
    del forbidden
    return list_identifiers(organization_id)


def main() -> int:
    if len(sys.argv) != 1:
        return _public("BLOCKED", "UNEXPECTED_ARGV")
    try:
        version = importlib.metadata.version("bitwarden-sdk")
        if version != PINNED_SDK_VERSION:
            return _public("BLOCKED", "SDK_VERSION_MISMATCH")
        access_token = _required_env("BWS_ACCESS_TOKEN")
        organization_id = _required_env("BWS_ORGANIZATION_ID")
        if UUID_RE.fullmatch(organization_id) is None:
            return _public("BLOCKED", "ORGANIZATION_ID_INVALID")
        expected_keys = _load_expected_keys()
        client = _sdk_client()
        _login(client, access_token)
        response = _list_identifiers(client, organization_id)
        identifiers = [
            _validate_identifier(_mapping_from_object(item), organization_id)
            for item in _response_data(response)
        ]
    except RuntimeError as exc:
        return _public("BLOCKED", str(exc))
    except Exception:
        return _public("BLOCKED", "SDK_IDENTIFIER_DISCOVERY_FAILED")

    matches = [item for item in identifiers if item["key"] in expected_keys]
    if not matches:
        return _public("BLOCKED", "IDENTIFIER_MATCH_ZERO", match_status="ZERO")
    if len(matches) != 1:
        return _public("BLOCKED", "IDENTIFIER_MATCH_AMBIGUOUS", match_status="MANY")
    selected = matches[0]
    return _public(
        "DONE",
        "OK",
        match_status="ONE",
        reference_id=selected["id"],
        matched_key=selected["key"],
    )


if __name__ == "__main__":
    raise SystemExit(main())
