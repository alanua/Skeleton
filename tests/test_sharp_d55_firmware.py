from __future__ import annotations

import hashlib
import zipfile

import pytest

from tools.sharp import verify_d55_v101 as sharp


def make_package(tmp_path, payload: bytes):
    package = tmp_path / sharp.ZIP_NAME
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(sharp.BIN_NAME, payload)
    return package


def test_verify_fails_closed_on_wrong_outer_hash(tmp_path):
    package = make_package(tmp_path, b"not-the-oem-image")
    with pytest.raises(ValueError, match="ZIP SHA-256 mismatch"):
        sharp.verify(package)


def test_verify_accepts_only_matching_outer_and_inner_hashes(tmp_path, monkeypatch):
    payload = b"synthetic-private-fixture"
    package = make_package(tmp_path, payload)
    monkeypatch.setattr(sharp, "ZIP_SHA256", hashlib.sha256(package.read_bytes()).hexdigest())
    monkeypatch.setattr(sharp, "BIN_SHA256", hashlib.sha256(payload).hexdigest())

    info, inner_hash = sharp.verify(package)

    assert info.filename == sharp.BIN_NAME
    assert inner_hash == hashlib.sha256(payload).hexdigest()


def test_extract_verified_rechecks_written_inner_image(tmp_path, monkeypatch):
    payload = b"synthetic-private-fixture"
    package = make_package(tmp_path, payload)
    monkeypatch.setattr(sharp, "ZIP_SHA256", hashlib.sha256(package.read_bytes()).hexdigest())
    monkeypatch.setattr(sharp, "BIN_SHA256", hashlib.sha256(payload).hexdigest())
    sharp.verify(package)

    output = sharp.extract_verified(package, tmp_path / "private-work")

    assert output.name == sharp.BIN_NAME
    assert output.read_bytes() == payload


def test_locate_inner_rejects_duplicate_basename(tmp_path):
    package = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(sharp.BIN_NAME, b"one")
        zf.writestr(f"nested/{sharp.BIN_NAME}", b"two")

    with zipfile.ZipFile(package, "r") as zf:
        with pytest.raises(ValueError, match="expected exactly one"):
            sharp.locate_inner(zf)
