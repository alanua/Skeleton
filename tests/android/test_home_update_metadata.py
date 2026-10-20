from __future__ import annotations

import json
from pathlib import Path

from core.android_home_update_metadata import (
    AndroidHomeCurrentApp,
    AndroidHomeUpdateDecisionState,
    CHANNEL_RULES,
    REQUIRED_METADATA_KEYS,
    decide_android_home_update,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas" / "android_home_update_metadata.schema.json"
SOURCE_SHA = "0123456789abcdef0123456789abcdef01234567"
APK_SHA = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"


def current_app(**overrides: object) -> AndroidHomeCurrentApp:
    values = {
        "source_sha": SOURCE_SHA,
        "version_code": 41,
        "package_id": "com.skeleton.home.preview",
        "update_channel": "operator",
    }
    values.update(overrides)
    return AndroidHomeCurrentApp(**values)


def metadata(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "source_sha": SOURCE_SHA,
        "artifact_scope": "android_home_operator_preview_apk",
        "version_code": 42,
        "version_name": "1.2.3-preview",
        "apk_sha256": APK_SHA,
        "apk_bytes": 12_345_678,
        "package_id": "com.skeleton.home.preview",
        "update_channel": "operator",
    }
    values.update(overrides)
    return values


def test_accepts_operator_preview_metadata_for_exact_current_source_and_monotonic_version() -> None:
    decision = decide_android_home_update(metadata(), current_app=current_app())

    assert decision.state is AndroidHomeUpdateDecisionState.ACCEPT
    assert decision.accepted is True
    assert decision.reasons == ()
    assert decision.metadata is not None
    assert decision.metadata.version_code == 42
    assert decision.current_app_unchanged is True


def test_accepts_family_metadata_only_with_family_package_scope_and_channel() -> None:
    decision = decide_android_home_update(
        metadata(
            artifact_scope="android_home_family_review_apk",
            package_id="com.skeleton.home",
            update_channel="family",
        ),
        current_app=current_app(package_id="com.skeleton.home", update_channel="family"),
    )

    assert decision.state is AndroidHomeUpdateDecisionState.ACCEPT


def test_rejects_malformed_metadata_fail_closed() -> None:
    decision = decide_android_home_update("{not-json", current_app=current_app())

    assert decision.state is AndroidHomeUpdateDecisionState.REJECT
    assert decision.accepted is False
    assert decision.metadata is None
    assert decision.reasons == ("metadata must be valid JSON.",)
    assert decision.current_app_unchanged is True


def test_rejects_missing_extra_and_wrong_typed_fields() -> None:
    payload = metadata()
    payload.pop("source_sha")
    payload["unexpected"] = "value"
    payload["version_code"] = True

    decision = decide_android_home_update(payload, current_app=current_app())

    assert decision.state is AndroidHomeUpdateDecisionState.REJECT
    assert decision.reasons == (
        "metadata missing required fields: source_sha.",
        "metadata contains unsupported fields: unexpected.",
        "source_sha must be a string.",
        "version_code must be an integer.",
    )


def test_rejects_invalid_hash_source_size_and_version_shape() -> None:
    decision = decide_android_home_update(
        metadata(
            source_sha="A" * 40,
            version_code=0,
            version_name="release candidate",
            apk_sha256="abc",
            apk_bytes=0,
        ),
        current_app=current_app(),
    )

    assert decision.state is AndroidHomeUpdateDecisionState.REJECT
    assert decision.reasons == (
        "source_sha must be a 40-character lowercase Git SHA.",
        "apk_sha256 must be a 64-character lowercase SHA-256 hex digest.",
        "version_code must be between 1 and 2100000000.",
        "version_name must be a compact dotted version string.",
        "apk_bytes must be between 1 and 500000000.",
    )


def test_rejects_wrong_operator_package_scope_or_channel_family_mix() -> None:
    decision = decide_android_home_update(
        metadata(package_id="com.skeleton.home", artifact_scope="android_home_family_review_apk"),
        current_app=current_app(),
    )

    assert decision.state is AndroidHomeUpdateDecisionState.REJECT
    assert decision.reasons == (
        "package_id must be com.skeleton.home.preview for operator channel.",
        "artifact_scope must be android_home_operator_preview_apk for operator channel.",
    )


def test_rejects_stale_source_identity_wrong_target_and_non_monotonic_version() -> None:
    decision = decide_android_home_update(
        metadata(source_sha="1" * 40, version_code=41),
        current_app=current_app(package_id="com.skeleton.home", update_channel="family"),
    )

    assert decision.state is AndroidHomeUpdateDecisionState.REJECT
    assert decision.metadata is not None
    assert decision.reasons == (
        "source_sha does not match the expected source identity.",
        "update_channel does not match the current app channel.",
        "package_id does not match the current app package.",
        "version_code must increase monotonically.",
    )


def test_rejects_invalid_current_app_identity_fail_closed() -> None:
    decision = decide_android_home_update(
        metadata(),
        current_app=current_app(source_sha="missing", version_code=0, update_channel="side_load"),
    )

    assert decision.state is AndroidHomeUpdateDecisionState.REJECT
    assert decision.reasons == (
        "current app source_sha must be a 40-character lowercase Git SHA.",
        "current app version_code must be positive.",
        "current app update_channel is not supported.",
        "source_sha does not match the expected source identity.",
        "update_channel does not match the current app channel.",
    )


def test_schema_documents_strict_metadata_and_channel_scope_rules() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$id"] == "skeleton.android_home_update_metadata.schema.json"
    assert set(schema["required"]) == REQUIRED_METADATA_KEYS
    assert schema["additionalProperties"] is False
    assert schema["properties"]["source_sha"]["pattern"] == "^[0-9a-f]{40}$"
    assert schema["properties"]["apk_sha256"]["pattern"] == "^[0-9a-f]{64}$"
    assert schema["properties"]["version_code"]["maximum"] == 2100000000
    assert schema["properties"]["apk_bytes"]["maximum"] == 500000000

    operator_rule, family_rule = schema["allOf"]
    assert operator_rule["then"]["properties"]["package_id"]["const"] == CHANNEL_RULES["operator"][0]
    assert operator_rule["then"]["properties"]["artifact_scope"]["const"] == CHANNEL_RULES["operator"][1]
    assert family_rule["then"]["properties"]["package_id"]["const"] == CHANNEL_RULES["family"][0]
    assert family_rule["then"]["properties"]["artifact_scope"]["const"] == CHANNEL_RULES["family"][1]
