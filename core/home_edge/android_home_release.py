from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


CURRENT_ANDROID_HOME_VERIFY_OPERATION = "home_native_apk_current_verify"
SUPERSEDED_ANDROID_HOME_VERIFY_OPERATIONS = ("home-native-apk-v134-verify",)
METADATA_PATH = Path("android/home/release_metadata.json")


def load_release_metadata(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_android_home_release(
    metadata: Mapping[str, Any],
    artifact_root: Path,
) -> dict[str, object]:
    version_name = metadata.get("version_name")
    canonical_name = metadata.get("canonical_apk")
    aliases = metadata.get("production_aliases")
    expected_sha = metadata.get("sha256")
    if not isinstance(version_name, str) or not version_name:
        return _receipt("metadata_invalid", version_name or "unknown", "", "", 0)
    if not isinstance(canonical_name, str) or not canonical_name:
        return _receipt("metadata_invalid", version_name, "", "", 0)
    if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
        return _receipt("metadata_invalid", version_name, canonical_name, "", 0)

    canonical = artifact_root / canonical_name
    if not canonical.is_file():
        return _receipt("canonical_apk_missing", version_name, canonical_name, "", 0)
    actual_sha = _sha256_file(canonical)
    if isinstance(expected_sha, str) and expected_sha and expected_sha != actual_sha:
        return _receipt("canonical_sha_mismatch", version_name, canonical_name, actual_sha, canonical.stat().st_size)
    for alias in aliases:
        alias_path = artifact_root / alias
        if not alias_path.is_file():
            return _receipt("alias_missing", version_name, canonical_name, actual_sha, canonical.stat().st_size)
        if _sha256_file(alias_path) != actual_sha:
            return _receipt("alias_sha_mismatch", version_name, canonical_name, actual_sha, canonical.stat().st_size)
    return _receipt("healthy", version_name, canonical_name, actual_sha, canonical.stat().st_size)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _receipt(
    status: str,
    version_name: object,
    canonical_apk: str,
    sha256: str,
    byte_size: int,
) -> dict[str, object]:
    return {
        "operation_id": CURRENT_ANDROID_HOME_VERIFY_OPERATION,
        "status": status,
        "version_name": str(version_name),
        "canonical_apk": canonical_apk,
        "sha256": sha256 or "unavailable",
        "byte_size": byte_size,
    }


def receipt_status_lines(receipt: Mapping[str, object]) -> list[str]:
    return [
        f"operation_id={receipt['operation_id']}",
        f"status={receipt['status']}",
        f"version_name={receipt['version_name']}",
        f"canonical_apk={receipt['canonical_apk']}",
        f"sha256={receipt['sha256']}",
        f"byte_size={receipt['byte_size']}",
    ]
