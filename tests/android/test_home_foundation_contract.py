import re
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANDROID_HOME = ROOT / "android" / "home"
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def read(relative: str) -> str:
    return (ANDROID_HOME / relative).read_text(encoding="utf-8")


def kotlin_sources() -> dict[str, str]:
    return {
        str(path.relative_to(ANDROID_HOME)): path.read_text(encoding="utf-8")
        for path in (ANDROID_HOME / "app" / "src" / "main").rglob("*.kt")
    }


def manifest_root() -> ET.Element:
    return ET.fromstring(read("app/src/main/AndroidManifest.xml"))


def android_attr(element: ET.Element, name: str) -> str | None:
    return element.attrib.get(f"{ANDROID_NS}{name}")


def test_package_identity_label_compose_and_main_entry_are_current() -> None:
    gradle = read("app/build.gradle.kts")
    manifest = manifest_root()
    main = read("app/src/main/java/com/skeleton/home/MainActivity.kt")

    assert 'namespace = "com.skeleton.home"' in gradle
    assert 'applicationId = "com.skeleton.home"' in gradle
    assert re.search(r"versionCode\s*=\s*(\d+)", gradle)
    assert int(re.search(r"versionCode\s*=\s*(\d+)", gradle).group(1)) > 0
    assert re.search(r'versionName\s*=\s*"\d+\.\d+\.\d+(?:[-+][^"]+)?"', gradle)
    assert "buildFeatures { compose = true; buildConfig = true }" in gradle
    assert "androidx.activity:activity-compose" in gradle

    application = manifest.find("application")
    assert application is not None
    assert android_attr(application, "label") == "Home"
    assert android_attr(application, "allowBackup") == "false"

    main_activity = manifest.find("./application/activity[@android:name='.MainActivity']", {"android": ANDROID_NS[1:-1]})
    assert main_activity is not None
    assert android_attr(main_activity, "exported") == "true"
    assert "<action android:name=\"android.intent.action.MAIN\"" in read("app/src/main/AndroidManifest.xml")
    assert "class MainActivity : ComponentActivity()" in main
    assert "import androidx.activity.compose.setContent" in main
    assert "setContent { MaterialTheme" in main
    assert "HomeComposeApp(sharedUrl.value)" in main


def test_declared_permissions_and_health_connect_contract_are_canonical() -> None:
    manifest = read("app/src/main/AndroidManifest.xml")
    rationale = read("app/src/main/java/com/skeleton/home/HealthPermissionsRationaleActivity.kt")
    health = read("app/src/main/java/com/skeleton/home/health/SkeletonHealthConnectAdapter.kt")

    for permission in [
        "android.permission.INTERNET",
        "android.permission.REQUEST_INSTALL_PACKAGES",
        "android.permission.health.READ_STEPS",
        "android.permission.health.READ_SLEEP",
    ]:
        assert f'<uses-permission android:name="{permission}"' in manifest

    assert '<package android:name="com.google.android.apps.healthdata"' in manifest
    assert 'android:name=".HealthPermissionsRationaleActivity"' in manifest
    assert 'androidx.health.ACTION_SHOW_PERMISSIONS_RATIONALE' in manifest
    assert 'android:name=".HealthPermissionsUsageActivity"' in manifest
    assert "android.permission.START_VIEW_PERMISSION_USAGE" in manifest
    assert "android.intent.category.HEALTH_PERMISSIONS" in manifest

    assert "HealthPermission.getReadPermission(StepsRecord::class)" in health
    assert "HealthPermission.getReadPermission(SleepSessionRecord::class)" in health
    assert "PermissionController.createRequestPermissionResultContract()" in health
    assert "readRecentSummary" in health
    assert "Home запитує лише читання кроків та сесій/стадій сну" in rationale
    assert "геолокація, повідомлення та інші медичні категорії не читаються" in rationale


def test_current_self_update_primitives_are_present() -> None:
    manifest = read("app/src/main/AndroidManifest.xml")
    main = read("app/src/main/java/com/skeleton/home/MainActivity.kt")
    receiver = read("app/src/main/java/com/skeleton/home/InstallStatusReceiver.kt")

    assert '<receiver android:name=".InstallStatusReceiver" android:exported="false"' in manifest
    assert "private data class HomeUpdateInfo" in main
    assert 'api.get("/api/native/app-update")' in main
    assert "it.versionCode>0" in main
    assert "it.sha256.length==64" in main
    assert "DownloadManager.Request(Uri.parse(url))" in main
    assert 'setMimeType("application/vnd.android.package-archive")' in main
    assert 'MessageDigest.getInstance("SHA-256")' in main
    assert "requestPackageUpdateInstall(uri)" in main
    assert "Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES" in main
    assert "PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)" in main
    assert "InstallStatusReceiver::class.java" in main
    assert 'const val ACTION_INSTALL_STATUS="com.skeleton.home.INSTALL_STATUS"' in receiver
    assert "PackageInstaller.STATUS_PENDING_USER_ACTION" in receiver
    assert "PackageInstaller.STATUS_SUCCESS" in receiver


def test_home_edge_endpoints_are_buildconfig_driven_without_private_literals() -> None:
    gradle = read("app/build.gradle.kts")
    main = read("app/src/main/java/com/skeleton/home/MainActivity.kt")
    location = read("app/src/main/java/com/skeleton/home/LocationTrackingService.kt")
    source = "\n".join(kotlin_sources().values()) + "\n" + gradle

    assert 'val homeEdgeBaseUrls = providers.gradleProperty("homeEdgeBaseUrls").orElse("").get()' in gradle
    assert 'buildConfigField("String", "HOME_EDGE_BASE_URLS", "\\"$escapedHomeEdgeBaseUrls\\"")' in gradle
    assert "BuildConfig.HOME_EDGE_BASE_URLS" in main
    assert "BuildConfig.HOME_EDGE_BASE_URLS" in location
    assert '.filter { it.startsWith("http://") || it.startsWith("https://") }' in main

    assert not re.search(r'HOME_EDGE_BASE_URLS",\s*"\\"https?://', gradle)
    assert not re.search(r"val\s+servers\s*=\s*listOf\([^)]*https?://", main, re.DOTALL)
    assert not re.search(r"URL\(\s*\"https?://", main)
    assert not re.search(r"\b(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b", source)
    assert ".ts.net" not in source


def test_no_embedded_credentials_tokens_or_secret_values() -> None:
    text_files = [
        ANDROID_HOME / "app" / "build.gradle.kts",
        ANDROID_HOME / "app" / "src" / "main" / "AndroidManifest.xml",
    ]
    text_files.extend(
        path
        for path in (ANDROID_HOME / "app" / "src" / "main").rglob("*")
        if path.is_file() and path.suffix in {".kt", ".kts", ".xml", ".json"}
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in text_files)

    literal_assignments = re.findall(
        r"""(?ix)
        \b(?:api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|bearer|password|passwd|private[_-]?key|client[_-]?secret)\b
        \s*(?:=|:)\s*["'][^"']{8,}["']
        """,
        source,
    )
    assert literal_assignments == []
    assert "Authorization\"" not in source
    assert "Bearer " not in source
    assert "sk-" not in source
    assert "BEGIN PRIVATE KEY" not in source
    assert "BEGIN OPENSSH PRIVATE KEY" not in source


def test_current_webview_and_native_bridge_are_intentional() -> None:
    main = read("app/src/main/java/com/skeleton/home/MainActivity.kt")

    assert "import android.webkit.WebView" in main
    assert "import android.webkit.JavascriptInterface" in main
    assert "AndroidView(" in main
    assert "WebView(ctx).apply" in main
    assert "@JavascriptInterface" in main
    assert "addJavascriptInterface" in main


def test_home_media_control_uses_explicit_endpoint_routing() -> None:
    main = read("app/src/main/java/com/skeleton/home/MainActivity.kt")

    assert "private data class MediaEndpointUi" in main
    assert 'MediaEndpointUi("home_edge_tv","TV","home_edge_tv"' in main
    assert 'MediaEndpointUi("samsung_tv","Samsung","samsung"' in main
    assert 'api.get("/api/media/endpoints")' in main
    assert 'private fun endpointMutation(endpointId:String' in main
    assert 'JSONObject().put("endpoint_id",endpointId)' in main
    assert 'private fun endpointPath(endpointId:String,suffix:String):String="/api/media/endpoints/${Uri.encode(endpointId)}$suffix"' in main
    assert "OutputTargetSelector(endpointId,endpoints,onEndpoint)" in main
    assert 'api.put("/api/media/target"' not in main
    assert 'api.get("/api/media/target"' not in main
    assert '"/api/samsung/media/' not in main
    assert 'api.post("/api/play"' not in main
    assert 'api.post("/api/volume"' not in main
    assert 'api.post("/api/tv/play"' not in main


def test_home_android_validation_does_not_autostart_tablet_video() -> None:
    main = read("app/src/main/java/com/skeleton/home/MainActivity.kt")

    assert "LaunchedEffect(api.server,selectionRevision" in main
    assert 'api.post(endpointPath(endpoint.endpointId,"/play"),o)' in main
    assert not re.search(r"LaunchedEffect\([^)]*\)\s*\{[^{}]*api\.post\(endpointPath\([^)]*\"/play\"", main)
