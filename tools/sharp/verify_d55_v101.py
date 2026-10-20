#!/usr/bin/env python3
"""Fail-closed verifier for the exact Sharp D55CF6352EB08E V1.01 OEM package.

This tool never modifies the input archive and never downloads firmware.  It only
accepts the exact operator-reviewed OEM hashes before optionally copying the
inner allupgrade image to a private working directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ZIP_NAME = "D55CF6352EB08E_V1.01.zip"
ZIP_SHA256 = "fe3e0c350b25cbbf0a0223bf613f40a21c83dc913e7591253e2a66696e508ca2"
BIN_NAME = "allupgrade_6308rtbm.bin"
BIN_SHA256 = "c1d01b0e76f022139278124ee158f8fe466a40310c0e53e43adc036d557924fd"
CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_stream(handle) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(CHUNK), b""):
        digest.update(chunk)
    return digest.hexdigest()


def locate_inner(zf: zipfile.ZipFile) -> zipfile.ZipInfo:
    matches = [info for info in zf.infolist() if Path(info.filename).name == BIN_NAME]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {BIN_NAME}, found {len(matches)}")
    info = matches[0]
    if info.is_dir():
        raise ValueError(f"{BIN_NAME} is not a regular archive member")
    return info


def verify(package: Path) -> tuple[zipfile.ZipInfo, str]:
    if not package.is_file():
        raise ValueError("OEM package does not exist or is not a regular file")
    actual_zip_hash = sha256_file(package)
    if actual_zip_hash != ZIP_SHA256:
        raise ValueError(f"ZIP SHA-256 mismatch: {actual_zip_hash}")
    with zipfile.ZipFile(package, "r") as zf:
        info = locate_inner(zf)
        with zf.open(info, "r") as inner:
            actual_bin_hash = sha256_stream(inner)
    if actual_bin_hash != BIN_SHA256:
        raise ValueError(f"BIN SHA-256 mismatch: {actual_bin_hash}")
    return info, actual_bin_hash


def extract_verified(package: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / BIN_NAME
    with zipfile.ZipFile(package, "r") as zf:
        info = locate_inner(zf)
        with zf.open(info, "r") as src, output.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=CHUNK)
    if sha256_file(output) != BIN_SHA256:
        output.unlink(missing_ok=True)
        raise ValueError("post-copy BIN verification failed")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path, help=f"private path to {ZIP_NAME}")
    parser.add_argument("--extract-bin", type=Path, metavar="DIR")
    args = parser.parse_args()

    try:
        info, inner_hash = verify(args.package)
        output = extract_verified(args.package, args.extract_bin) if args.extract_bin else None
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2

    result = {
        "ok": True,
        "package_name_expected": ZIP_NAME,
        "zip_sha256": ZIP_SHA256,
        "inner_member": info.filename,
        "inner_sha256": inner_hash,
        "inner_size": info.file_size,
        "extracted_to": str(output) if output else None,
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
