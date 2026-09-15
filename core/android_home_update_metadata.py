from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


SOURCE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
APK_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VERSION_NAME_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9][A-Za-z0-9._-]{0,63})?$")
MAX_ANDROID_VERSION_CODE = 2_100_000_000
MAX_APK_BYTES = 500_000_000


class AndroidHomeUpdateDecisionState(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class AndroidHomeUpdateChannel(StrEnum):
    OPERATOR = "operator"
    FAMILY = "family"


class AndroidHomeArtifactScope(StrEnum):
    OPERATOR_PREVIEW_APK = "android_home_operator_preview_apk"
    FAMILY_REVIEW_APK = "android_home_family_review_apk"


CHANNEL_RULES: Mapping[str, tuple[str, str]] = {
    AndroidHomeUpdateChannel.OPERATOR.value: (
        "com.skeleton.home.preview",
        AndroidHomeArtifactScope.OPERATOR_PREVIEW_APK.value,
    ),
    AndroidHomeUpdateChannel.FAMILY.value: (
        "com.skeleton.home",
        AndroidHomeArtifactScope.FAMILY_REVIEW_APK.value,
    ),
}

REQUIRED_METADATA_KEYS = frozenset(
    {
        "source_sha",
        "artifact_scope",
        "version_code",
        "version_name",
        "apk_sha256",
        "apk_bytes",
        "package_id",
        "update_channel",
    }
)


@dataclass(frozen=True)
class AndroidHomeUpdateMetadata:
    source_sha: str
    artifact_scope: str
    version_code: int
    version_name: str
    apk_sha256: str
    apk_bytes: int
    package_id: str
    update_channel: str


@dataclass(frozen=True)
class AndroidHomeCurrentApp:
    source_sha: str
    version_code: int
    package_id: str
    update_channel: str


@dataclass(frozen=True)
class AndroidHomeUpdateDecision:
    state: AndroidHomeUpdateDecisionState
    metadata: AndroidHomeUpdateMetadata | None
    reasons: tuple[str, ...]
    current_app_unchanged: bool = True

    @property
    def accepted(self) -> bool:
        return self.state is AndroidHomeUpdateDecisionState.ACCEPT


def decide_android_home_update(
    payload: Mapping[str, Any] | bytes | str,
    *,
    current_app: AndroidHomeCurrentApp,
) -> AndroidHomeUpdateDecision:
    raw_metadata, parse_reasons = _coerce_mapping(payload)
    if raw_metadata is None:
        return _reject(parse_reasons)

    metadata, metadata_reasons = _validate_metadata(raw_metadata)
    if metadata is None:
        return _reject(metadata_reasons)

    decision_reasons = _validate_against_current_app(metadata, current_app)
    if decision_reasons:
        return _reject(decision_reasons, metadata)

    return AndroidHomeUpdateDecision(
        state=AndroidHomeUpdateDecisionState.ACCEPT,
        metadata=metadata,
        reasons=(),
    )


def _coerce_mapping(payload: Mapping[str, Any] | bytes | str) -> tuple[Mapping[str, Any] | None, tuple[str, ...]]:
    if isinstance(payload, Mapping):
        return payload, ()
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None, ("metadata must be UTF-8 JSON.",)
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return None, ("metadata must be valid JSON.",)
        if not isinstance(decoded, Mapping):
            return None, ("metadata must be a JSON object.",)
        return decoded, ()
    return None, ("metadata must be a mapping or JSON object.",)


def _validate_metadata(raw: Mapping[str, Any]) -> tuple[AndroidHomeUpdateMetadata | None, tuple[str, ...]]:
    reasons: list[str] = []
    raw_keys = set(raw)
    missing = REQUIRED_METADATA_KEYS - raw_keys
    extra = raw_keys - REQUIRED_METADATA_KEYS
    if missing:
        reasons.append(f"metadata missing required fields: {', '.join(sorted(missing))}.")
    if extra:
        reasons.append(f"metadata contains unsupported fields: {', '.join(sorted(extra))}.")

    source_sha = _string_field(raw, "source_sha", reasons)
    artifact_scope = _string_field(raw, "artifact_scope", reasons)
    version_code = _integer_field(raw, "version_code", reasons)
    version_name = _string_field(raw, "version_name", reasons)
    apk_sha256 = _string_field(raw, "apk_sha256", reasons)
    apk_bytes = _integer_field(raw, "apk_bytes", reasons)
    package_id = _string_field(raw, "package_id", reasons)
    update_channel = _string_field(raw, "update_channel", reasons)

    if source_sha is not None and SOURCE_SHA_PATTERN.fullmatch(source_sha) is None:
        reasons.append("source_sha must be a 40-character lowercase Git SHA.")
    if apk_sha256 is not None and APK_SHA256_PATTERN.fullmatch(apk_sha256) is None:
        reasons.append("apk_sha256 must be a 64-character lowercase SHA-256 hex digest.")
    if version_code is not None and not 1 <= version_code <= MAX_ANDROID_VERSION_CODE:
        reasons.append("version_code must be between 1 and 2100000000.")
    if version_name is not None:
        if not 1 <= len(version_name) <= 80 or VERSION_NAME_PATTERN.fullmatch(version_name) is None:
            reasons.append("version_name must be a compact dotted version string.")
    if apk_bytes is not None and not 1 <= apk_bytes <= MAX_APK_BYTES:
        reasons.append("apk_bytes must be between 1 and 500000000.")

    if update_channel is not None and update_channel not in CHANNEL_RULES:
        reasons.append("update_channel must be one of: family, operator.")
    if update_channel in CHANNEL_RULES:
        expected_package_id, expected_scope = CHANNEL_RULES[update_channel]
        if package_id is not None and package_id != expected_package_id:
            reasons.append(f"package_id must be {expected_package_id} for {update_channel} channel.")
        if artifact_scope is not None and artifact_scope != expected_scope:
            reasons.append(f"artifact_scope must be {expected_scope} for {update_channel} channel.")

    if reasons:
        return None, tuple(reasons)

    return (
        AndroidHomeUpdateMetadata(
            source_sha=source_sha or "",
            artifact_scope=artifact_scope or "",
            version_code=version_code or 0,
            version_name=version_name or "",
            apk_sha256=apk_sha256 or "",
            apk_bytes=apk_bytes or 0,
            package_id=package_id or "",
            update_channel=update_channel or "",
        ),
        (),
    )


def _validate_against_current_app(
    metadata: AndroidHomeUpdateMetadata,
    current_app: AndroidHomeCurrentApp,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if SOURCE_SHA_PATTERN.fullmatch(current_app.source_sha) is None:
        reasons.append("current app source_sha must be a 40-character lowercase Git SHA.")
    if current_app.version_code < 1:
        reasons.append("current app version_code must be positive.")
    if current_app.update_channel not in CHANNEL_RULES:
        reasons.append("current app update_channel is not supported.")
    elif current_app.package_id != CHANNEL_RULES[current_app.update_channel][0]:
        reasons.append("current app package_id does not match its update_channel.")

    if metadata.source_sha != current_app.source_sha:
        reasons.append("source_sha does not match the expected source identity.")
    if metadata.update_channel != current_app.update_channel:
        reasons.append("update_channel does not match the current app channel.")
    if metadata.package_id != current_app.package_id:
        reasons.append("package_id does not match the current app package.")
    if metadata.version_code <= current_app.version_code:
        reasons.append("version_code must increase monotonically.")
    return tuple(reasons)


def _string_field(raw: Mapping[str, Any], name: str, reasons: list[str]) -> str | None:
    value = raw.get(name)
    if not isinstance(value, str):
        reasons.append(f"{name} must be a string.")
        return None
    return value


def _integer_field(raw: Mapping[str, Any], name: str, reasons: list[str]) -> int | None:
    value = raw.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        reasons.append(f"{name} must be an integer.")
        return None
    return value


def _reject(
    reasons: tuple[str, ...],
    metadata: AndroidHomeUpdateMetadata | None = None,
) -> AndroidHomeUpdateDecision:
    return AndroidHomeUpdateDecision(
        state=AndroidHomeUpdateDecisionState.REJECT,
        metadata=metadata,
        reasons=reasons,
    )
