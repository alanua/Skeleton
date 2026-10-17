"""Offline deterministic reviewed-source ZIP staging. Never installs or starts agents."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

# Only public-safe agent contracts. No device data, secrets, native CAD DLLs.
FILES = (
    "core/workstation_agent.py",
    "core/workstation_signed_gate.py",
    "core/workstation_receipt_store.py",
    "adapters/workstation_control.py",
    "schemas/workstation_command.schema.json",
    "scripts/workstation_preflight.py",
    "docs/WORKSTATION_AGENT.md",
)
_SHA = re.compile(r"^[0-9a-f]{40}$")


class BundleRejected(ValueError):
    pass


def _git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(["git", "-C", str(root), *args],
                                       stderr=subprocess.DEVNULL, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BundleRejected("GIT_SOURCE_UNAVAILABLE") from exc


def build_bundle(root: Path, destination: Path, *, expected_head: str) -> dict[str, object]:
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise BundleRejected("INVALID_SOURCE_ROOT")
    if not isinstance(destination, Path) or destination.is_symlink():
        raise BundleRejected("INVALID_BUNDLE_PATH")
    if not _SHA.fullmatch(expected_head):
        raise BundleRejected("INVALID_EXACT_HEAD")
    if _git(root, "rev-parse", "--show-toplevel").decode().strip() != str(root.resolve()):
        raise BundleRejected("SOURCE_NOT_REPO_ROOT")
    if _git(root, "rev-parse", "HEAD").decode().strip() != expected_head:
        raise BundleRejected("HEAD_MISMATCH")
    if _git(root, "status", "--porcelain").strip():
        raise BundleRejected("DIRTY_SOURCE_TREE")
    if destination.resolve().is_relative_to(root.resolve()):
        raise BundleRejected("BUNDLE_INSIDE_SOURCE_REPO")
    blobs: dict[str, bytes] = {}
    for path in FILES:
        actual = root / path
        if not actual.is_file() or actual.is_symlink():
            raise BundleRejected("MISSING_OR_LINKED_SOURCE")
        checked = _git(root, "show", expected_head + ":" + path)
        data = actual.read_bytes()
        if data != checked:
            raise BundleRejected("SOURCE_BYTES_MISMATCH")
        blobs[path] = data
    manifest = {
        "schema": "skeleton.workstation_bundle.v1",
        "source_head": expected_head,
        "source_only": True,
        "install_ready": False,  # requires real issuer/transport/private adapters
        "files": {p: hashlib.sha256(blobs[p]).hexdigest() for p in FILES},
    }
    data = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if not destination.parent.is_dir():
        raise BundleRejected("BUNDLE_DIRECTORY_MISSING")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as zipf:
        for path, contents in (*[(p, blobs[p]) for p in FILES], ("MANIFEST.json", data)):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (0o100644 << 16)
            zipf.writestr(info, contents)
    return {"schema": manifest["schema"], "source_head": expected_head,
            "file_count": len(FILES), "archive_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "install_ready": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage review ZIP, never install")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-head", required=True)
    a = parser.parse_args()
    print(json.dumps(build_bundle(a.source, a.output, expected_head=a.expected_head),
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
