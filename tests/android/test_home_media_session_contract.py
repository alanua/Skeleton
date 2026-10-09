import re
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANDROID_HOME = ROOT / "android" / "home"
ADAPTER = ANDROID_HOME / "app/src/main/java/com/skeleton/home/MediaSessionSnapshotAdapter.kt"
MANIFEST = ANDROID_HOME / "app/src/main/AndroidManifest.xml"
MAIN = ANDROID_HOME / "app/src/main/java/com/skeleton/home/MainActivity.kt"
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def manifest_root() -> ET.Element:
    return ET.fromstring(read(MANIFEST))


def android_attr(element: ET.Element, name: str) -> str | None:
    return element.attrib.get(f"{ANDROID_NS}{name}")


def test_media_session_snapshot_adapter_is_separate_read_only_surface() -> None:
    source = read(ADAPTER)
    main = read(MAIN)

    assert "class MediaSessionSnapshotAdapter" in source
    assert "android.media.session.MediaSessionManager" in source
    assert "android.media.session.MediaController" in source
    assert "android.media.session.PlaybackState" in source
    assert "android.media.MediaMetadata" in source
    assert "MediaSessionSnapshotAdapter" not in main
    assert "fun MediaSessionSnapshotResult.toSkeletonJson()" in source

    forbidden_mutations = [
        ".transportControls",
        "dispatchMediaButtonEvent",
        "adjustVolume",
        "setVolumeTo",
        "sendCommand",
        "play(",
        "pause(",
        "skipTo",
        "seekTo",
    ]
    for forbidden in forbidden_mutations:
        assert forbidden not in source


def test_media_session_snapshot_requires_android_permission_before_observation() -> None:
    source = read(ADAPTER)

    assert '"android.permission.MEDIA_CONTENT_CONTROL"' in source
    assert "fun hasMediaSessionPermission(context: Context): Boolean" in source
    assert "context.checkSelfPermission(MEDIA_CONTENT_CONTROL_PERMISSION)" in source
    assert "PackageManager.PERMISSION_GRANTED" in source
    assert "if (!hasMediaSessionPermission(context))" in source
    assert "MediaSessionSnapshotAvailability.PERMISSION_DENIED" in source
    assert re.search(
        r"catch\s*\(_:\s*SecurityException\)\s*\{\s*return MediaSessionSnapshotResult\(",
        source,
        re.DOTALL,
    )


def test_media_session_snapshot_has_allowlist_before_metadata_reading() -> None:
    source = read(ADAPTER)

    assert "allowedMediaPackages: Set<String> = emptySet()" in source
    assert "if (allowedMediaPackages.isEmpty())" in source
    assert ".filter { it.packageName in allowedMediaPackages }" in source
    assert ".firstOrNull()" in source

    filter_index = source.index(".filter { it.packageName in allowedMediaPackages }")
    title_index = source.index("MediaMetadata.METADATA_KEY_TITLE")
    duration_index = source.index("MediaMetadata.METADATA_KEY_DURATION")
    assert filter_index < title_index
    assert filter_index < duration_index

    assert "NotificationListenerService" not in source
    assert "StatusBarNotification" not in source
    assert "android.service.notification" not in source


def test_media_session_snapshot_redacts_to_typed_minimal_public_state() -> None:
    source = read(ADAPTER)

    assert "enum class MediaSessionPlaybackStatus" in source
    assert "PLAYING" in source
    assert "PAUSED" in source
    assert "IDLE" in source
    assert "val collectedAtMillis: Long" in source
    assert "val positionMillis: Long?" in source
    assert "val durationMillis: Long?" in source
    assert "val title: String?" in source
    assert "private const val MAX_TITLE_LENGTH = 120" in source
    assert "filterNot { Character.isISOControl(it) }" in source
    assert ".take(MAX_TITLE_LENGTH)" in source

    disallowed_metadata = [
        "METADATA_KEY_ARTIST",
        "METADATA_KEY_ALBUM",
        "METADATA_KEY_ALBUM_ART",
        "METADATA_KEY_ART",
        "METADATA_KEY_DISPLAY_ICON",
        "METADATA_KEY_DISPLAY_SUBTITLE",
        "METADATA_KEY_DISPLAY_DESCRIPTION",
    ]
    for key in disallowed_metadata:
        assert key not in source


def test_media_session_manifest_contract_has_no_new_privileged_or_listener_entries() -> None:
    manifest_text = read(MANIFEST)
    root = manifest_root()

    assert "MEDIA_CONTENT_CONTROL" not in manifest_text
    assert "NotificationListenerService" not in manifest_text
    assert "android.service.notification.NotificationListenerService" not in manifest_text
    assert "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE" not in manifest_text

    services = root.findall("./application/service")
    for service in services:
        assert android_attr(service, "permission") != "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE"


def test_media_session_contract_avoids_notification_scraping_and_termux_paths() -> None:
    source = read(ADAPTER)
    all_home_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ANDROID_HOME / "app/src/main").rglob("*")
        if path.is_file() and path.suffix in {".kt", ".java", ".xml"}
    )

    assert "Notification" not in source
    assert "extras" not in source
    assert "contentResolver.query" not in source
    assert "/data/data/com.termux" not in all_home_sources
    assert "com.termux" not in source.lower()
