import re
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANDROID_HOME = ROOT / "android" / "home"
ADAPTER = ANDROID_HOME / "app/src/main/java/com/skeleton/home/MediaSessionSnapshotAdapter.kt"
LISTENER = ANDROID_HOME / "app/src/main/java/com/skeleton/home/MediaSessionObservationListener.kt"
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
    listener = read(LISTENER)
    main = read(MAIN)

    assert "class MediaSessionSnapshotAdapter" in source
    assert "class MediaSessionObservationListener : NotificationListenerService()" in listener
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
        assert forbidden not in listener


def test_media_session_snapshot_requires_enabled_listener_before_observation() -> None:
    source = read(ADAPTER)

    assert "android.permission.MEDIA_CONTENT_CONTROL" not in source
    assert "PackageManager.PERMISSION_GRANTED" not in source
    assert "fun isObservationListenerEnabled(context: Context): Boolean" in source
    assert 'Settings.Secure.getString(' in source
    assert '"enabled_notification_listeners"' in source
    assert "Settings.Secure.ENABLED_NOTIFICATION_LISTENERS" not in source
    assert "ComponentName(context, MediaSessionObservationListener::class.java)" in source
    assert "if (!isObservationListenerEnabled(context))" in source
    assert "MediaSessionSnapshotAvailability.LISTENER_DISABLED" in source
    assert "manager.getActiveSessions(ComponentName(context, MediaSessionObservationListener::class.java))" in source
    assert re.search(
        r"catch\s*\(_:\s*SecurityException\)\s*\{\s*return MediaSessionSnapshotResult\(",
        source,
        re.DOTALL,
    )


def test_media_session_snapshot_has_allowlist_before_metadata_reading() -> None:
    source = read(ADAPTER)

    assert "allowedMediaPackages: Set<String> = emptySet()" in source
    assert "if (allowedMediaPackages.isEmpty())" in source
    assert "if (!isObservationListenerEnabled(context))" in source
    assert ".filter { it.packageName in allowedMediaPackages }" in source
    assert ".firstOrNull()" in source

    allowlist_index = source.index("if (allowedMediaPackages.isEmpty())")
    enabled_index = source.index("if (!isObservationListenerEnabled(context))")
    query_index = source.index("manager.getActiveSessions(")
    filter_index = source.index(".filter { it.packageName in allowedMediaPackages }")
    title_index = source.index("MediaMetadata.METADATA_KEY_TITLE")
    duration_index = source.index("MediaMetadata.METADATA_KEY_DURATION")
    assert allowlist_index < enabled_index < query_index
    assert filter_index < title_index
    assert filter_index < duration_index

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


def test_media_session_manifest_contract_registers_inert_listener_without_privileged_media_permission() -> None:
    manifest_text = read(MANIFEST)
    root = manifest_root()

    assert "MEDIA_CONTENT_CONTROL" not in manifest_text
    assert "android.permission.MEDIA_CONTENT_CONTROL" not in manifest_text
    assert "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE" in manifest_text
    assert "android.service.notification.NotificationListenerService" in manifest_text

    services = [
        service
        for service in root.findall("./application/service")
        if android_attr(service, "name") == ".MediaSessionObservationListener"
    ]
    assert len(services) == 1
    service = services[0]
    assert android_attr(service, "permission") == "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE"
    assert android_attr(service, "exported") == "true"
    actions = [
        android_attr(action, "name")
        for action in service.findall("./intent-filter/action")
    ]
    assert actions == ["android.service.notification.NotificationListenerService"]


def test_media_session_contract_avoids_notification_scraping_and_termux_paths() -> None:
    source = read(ADAPTER)
    listener = read(LISTENER)
    all_home_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ANDROID_HOME / "app/src/main").rglob("*")
        if path.is_file() and path.suffix in {".kt", ".java", ".xml"}
    )

    assert "Notification" not in source
    assert "StatusBarNotification" not in listener
    assert "onNotificationPosted" not in listener
    assert "onNotificationRemoved" not in listener
    assert "extras" not in source
    assert "extras" not in listener
    assert "contentResolver.query" not in source
    assert "/data/data/com.termux" not in all_home_sources
    assert "com.termux" not in source.lower()
