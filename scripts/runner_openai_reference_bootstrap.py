#!/usr/bin/env python3
"""Install the one registered Runner OpenAI reference as an encrypted credential."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import uuid


SERVICE_ID = "runner-codex-openai"
ALIAS = "api-key"
PROVIDER = "bitwarden"
SCHEMA = "skeleton.secret_reference_index.v1"
CREDENTIAL_NAME = "skeleton-secret-reference-index"
ENCRYPTED_INDEX_PATH = Path(
    "/etc/skeleton/credstore.encrypted/skeleton-secret-reference-index.cred"
)
SYSTEMD_CREDS = "/usr/bin/systemd-creds"


def _read_reference() -> str:
    if len(sys.argv) != 1:
        raise ValueError("arguments_not_allowed")
    raw = sys.stdin.read()
    if not raw.endswith("\n") or raw.count("\n") != 1:
        raise ValueError("invalid_input")
    value = raw[:-1]
    parsed = uuid.UUID(value)
    if str(parsed) != value:
        raise ValueError("invalid_input")
    return value


def _empty_index() -> dict[str, object]:
    return {"schema": SCHEMA, "registrations": []}


def _load_existing_index() -> dict[str, object]:
    if ENCRYPTED_INDEX_PATH.is_symlink():
        raise ValueError("unsafe_index_path")
    if not ENCRYPTED_INDEX_PATH.exists():
        return _empty_index()
    if not ENCRYPTED_INDEX_PATH.is_file():
        raise ValueError("unsafe_index_path")
    result = subprocess.run(
        [SYSTEMD_CREDS, "decrypt", str(ENCRYPTED_INDEX_PATH), "-"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("decrypt_failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_existing_index") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema", "registrations"}:
        raise ValueError("invalid_existing_index")
    registrations = payload.get("registrations")
    if payload.get("schema") != SCHEMA or not isinstance(registrations, list):
        raise ValueError("invalid_existing_index")
    for registration in registrations:
        if not isinstance(registration, dict) or set(registration) != {
            "service_id",
            "alias",
            "provider",
            "reference_id",
        }:
            raise ValueError("invalid_existing_index")
    return payload


def _merged_index(payload: dict[str, object], reference_id: str) -> dict[str, object]:
    registrations = payload["registrations"]
    assert isinstance(registrations, list)
    preserved = [
        item
        for item in registrations
        if not (
            item["service_id"] == SERVICE_ID
            and item["alias"] == ALIAS
        )
    ]
    preserved.append(
        {
            "service_id": SERVICE_ID,
            "alias": ALIAS,
            "provider": PROVIDER,
            "reference_id": reference_id,
        }
    )
    if len(preserved) > 32:
        raise ValueError("index_capacity_exceeded")
    return {"schema": SCHEMA, "registrations": preserved}


def _encrypt_index(payload: dict[str, object]) -> None:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    result = subprocess.run(
        [
            SYSTEMD_CREDS,
            "encrypt",
            f"--name={CREDENTIAL_NAME}",
            "-",
            str(ENCRYPTED_INDEX_PATH),
        ],
        input=serialized,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("encrypt_failed")


def main() -> int:
    try:
        reference_id = _read_reference()
        payload = _merged_index(_load_existing_index(), reference_id)
        _encrypt_index(payload)
    except (OSError, subprocess.SubprocessError, ValueError):
        sys.stdout.write("status=FAILED\n")
        return 1
    sys.stdout.write("status=UPDATED\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
