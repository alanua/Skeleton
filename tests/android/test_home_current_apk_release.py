from __future__ import annotations

import hashlib

from core.home_edge import android_home_release as release


def test_android_home_release_verifier_derives_hash_from_current_metadata_and_aliases(
    tmp_path,
) -> None:
    payload = b"current production apk bytes"
    digest = hashlib.sha256(payload).hexdigest()
    for name in ("Home-1.3.16.apk", "Home.apk", "SkeletonTV.apk"):
        (tmp_path / name).write_bytes(payload)

    receipt = release.verify_android_home_release(
        {
            "version_name": "1.3.16",
            "canonical_apk": "Home-1.3.16.apk",
            "production_aliases": ["Home.apk", "SkeletonTV.apk"],
            "sha256": digest,
        },
        tmp_path,
    )

    assert receipt == {
        "operation_id": release.CURRENT_ANDROID_HOME_VERIFY_OPERATION,
        "status": "healthy",
        "version_name": "1.3.16",
        "canonical_apk": "Home-1.3.16.apk",
        "sha256": digest,
        "byte_size": len(payload),
    }


def test_android_home_release_verifier_rejects_alias_drift(tmp_path) -> None:
    (tmp_path / "Home-1.3.16.apk").write_bytes(b"current")
    (tmp_path / "Home.apk").write_bytes(b"current")
    (tmp_path / "SkeletonTV.apk").write_bytes(b"old")

    receipt = release.verify_android_home_release(
        {
            "version_name": "1.3.16",
            "canonical_apk": "Home-1.3.16.apk",
            "production_aliases": ["Home.apk", "SkeletonTV.apk"],
        },
        tmp_path,
    )

    assert receipt["status"] == "alias_sha_mismatch"
    assert "home-native-apk-v134-verify" in release.SUPERSEDED_ANDROID_HOME_VERIFY_OPERATIONS
