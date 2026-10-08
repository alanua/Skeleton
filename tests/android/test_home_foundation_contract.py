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
    assert re.search(r"versionCode\s*=\s*151\b", gradle)
    assert 'versionName = "1.4.35"' in gradle
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
    assert "private data class MediaTargetState" in main
    assert 'MediaEndpointUi("home_edge_tv","TV","home_edge_tv"' in main
    assert 'MediaEndpointUi("samsung_tv","Samsung","samsung"' in main
    assert 'api.get("/api/media/target")' in main
    assert 'api.post("/api/media/target",JSONObject().put("target",selected).put("endpoint_id",selected))' in main
    assert 'j.optJSONArray("targets") ?: j.optJSONArray("endpoints")' in main
    assert 'charged=j.optBoolean("charged",false)' in main
    assert 'private fun endpointMutation(endpointId:String' in main
    assert 'JSONObject().put("endpoint_id",endpointId)' in main
    assert 'private fun endpointPath(endpointId:String,suffix:String):String="/api/media/endpoints/${Uri.encode(endpointId)}$suffix"' in main
    assert "OutputTargetSelector(endpointId,endpoints,onEndpoint)" in main
    assert 'api.post("/api/media/handoff",JSONObject().put("action","capture").put("source",endpoint.endpointId))' in main
    assert 'api.post("/api/media/handoff",JSONObject().put("target",endpoint.endpointId))' in main
    assert 'endpointPath(endpoint.endpointId,"/handoff/capture")' not in main
    assert 'api.put("/api/media/target"' not in main
    assert '"/api/samsung/media/' not in main
    assert 'api.post("/api/play"' not in main
    assert 'api.post("/api/volume"' not in main
    assert 'api.post("/api/tv/play"' not in main


def test_home_header_keeps_capture_and_hyperion_on_home_and_video() -> None:
    main = read("app/src/main/java/com/skeleton/home/MainActivity.kt")
    hand_open = read("app/src/main/res/drawable/hand_open.xml")
    hand_fist = read("app/src/main/res/drawable/hand_fist.xml")

    assert "OutputTargetSelector(endpointId,endpoints,onEndpoint)" in main
    assert "captureCharged:Boolean=false" in main
    assert "captureEnabled:Boolean=false" in main
    assert "onCapture:(()->Unit)?=null" in main
    assert '"handoff" in endpoint.capabilities||"capture" in endpoint.capabilities' in main
    assert '"hand-open" -> R.drawable.hand_open' in main
    assert '"hand-fist" -> R.drawable.hand_fist' in main
    assert 'Mdi(if(captureCharged)"hand-fist" else "hand-open",22.dp,if(captureCharged)Accent else Color.White)' in main
    assert "captureCharged=captureCharged,captureEnabled=captureEnabled,onCapture={capturePress()}" in main
    assert "hyperion:Boolean?=null" in main
    assert "onHyperion:(()->Unit)?=null" in main
    assert "IconButton(onClick=onHyperion" in main
    assert 'Mdi(if(hyperion)"lightbulb-on-outline" else "lightbulb-outline",22.dp,if(hyperion)Accent else Color.White)' in main
    assert "Header(ui(\"Головна\"),api.server!=null,endpoint.endpointId,endpoints,onEndpoint,onMenu" in main
    assert "Header(ui(\"Відео\"),api.server!=null,endpoint.endpointId,endpoints,onEndpoint,onMenu" in main
    assert main.count("captureCharged=captureCharged,captureEnabled=captureEnabled,onCapture={capturePress()}") == 2
    assert main.count("hyperion=hyperion,onHyperion={onHyperion(!hyperion)}") == 2
    assert main.count('api.post("/api/hyperion"') == 1
    assert "<vector" in hand_open and "<path" in hand_open
    assert "<vector" in hand_fist and "<path" in hand_fist
    assert hand_open != hand_fist

    header_body = re.search(
        r"private fun Header\(.*?\)\s*\{(?P<body>.*?)\n\}\n\n\n@Composable private fun OutputTargetSelector",
        main,
        re.DOTALL,
    ).group("body")
    assert "SkeletonSwitch" not in header_body
    assert "keyboard-return" not in header_body
    assert 'enabled=captureEnabled' in header_body
    assert '"hand-fist" else "hand-open"' in header_body
    assert 'if(hyperion)"lightbulb-on-outline" else "lightbulb-outline"' in header_body

    for signature in [
        "private fun VideoScreen(api:HomeApi,active:Boolean,selectionRevision:Int,endpointId:String",
        "private fun FamilyScannerScreen(api:HomeApi,active:Boolean,endpointId:String",
        "private fun HomeEdgeScreen(api:HomeApi,endpointId:String",
        "private fun DevicesScreen(api:HomeApi,active:Boolean,endpointId:String",
        "private fun SkeletonScreen(api:HomeApi,active:Boolean,endpointId:String",
    ]:
        assert signature in main

    assert 'Header(ui("Відео"),api.server!=null,endpoint.endpointId,endpoints,onEndpoint,onMenu' in main
    assert 'captureCharged=captureCharged,captureEnabled=captureEnabled,onCapture={capturePress()},hyperion=hyperion,onHyperion={onHyperion(!hyperion)}' in main
    assert 'Header(ui("Сканувати")' in main
    assert 'Header("Home Edge"' in main
    assert 'Header(ui("Пристрої")' not in main
    assert 'Header(ui("СК")' not in main


def test_capture_handoff_state_is_backend_authoritative() -> None:
    main = read("app/src/main/java/com/skeleton/home/MainActivity.kt")

    assert "var mediaCharged by remember { mutableStateOf(false) }" in main
    assert "suspend fun refreshMediaTarget():Boolean" in main
    assert "mediaCharged=latest.charged" in main
    assert "onFailure = { false }" in main
    assert 'if(api.server!=null)runCatching{api.post("/api/media/target",JSONObject().put("target",selected).put("endpoint_id",selected))}' in main

    selector_change = re.search(
        r"fun selectMediaEndpoint\(selected:String\)\{(?P<body>.*?)\n    \}",
        main,
        re.DOTALL,
    ).group("body")
    assert "/api/media/handoff" not in selector_change
    assert "mediaCharged" not in selector_change

    capture_press = re.search(
        r"fun capturePress\(\)\{(?P<body>.*?)\n    \}\n    Box\(Modifier\.fillMaxSize",
        main,
        re.DOTALL,
    ).group("body")
    assert 'JSONObject().put("action","capture").put("source",endpoint.endpointId)' in capture_press
    assert 'JSONObject().put("target",endpoint.endpointId)' in capture_press
    assert 'api.post("/api/media/handoff"' in capture_press
    assert 'endpointPath(' not in capture_press
    assert 'onSuccess{out->' in capture_press
    assert 'if(out.has("charged"))onCaptureCharged(out.optBoolean("charged"))' in capture_press
    assert "refreshMediaTarget()" in capture_press
    assert "onFailure" not in capture_press


def test_home_android_validation_does_not_autostart_tablet_video() -> None:
    main = read("app/src/main/java/com/skeleton/home/MainActivity.kt")

    assert "LaunchedEffect(api.server,selectionRevision" in main
    assert 'api.post(endpointPath(endpoint.endpointId,"/play"),o)' in main
    assert not re.search(r"LaunchedEffect\([^)]*\)\s*\{[^{}]*api\.post\(endpointPath\([^)]*\"/play\"", main)


def test_home_video_selection_freshness_polls_lightweight_endpoint_selection_only() -> None:
    main = read("app/src/main/java/com/skeleton/home/MainActivity.kt")

    assert "private fun videoSelectionIdentity(selection:JSONObject):String=" in main
    assert 'listOf("job_id","source_id","season","episode","voice")' in main
    assert "var selectionIdentity by remember{mutableStateOf(\"\")}" in main
    assert "suspend fun applyEndpointSelection(latest:JSONObject,reloadWork:Boolean)" in main
    assert "selectionIdentity=videoSelectionIdentity(latest)" in main
    assert "if(reloadWork&&jobId.isNotBlank()){loadJob(jobId);loadMonitor()}" in main
    assert "LaunchedEffect(api.server,active,endpoint.endpointId)" in main
    assert "if(api.server!=null&&active)while(true)" in main
    assert "runCatching{endpointVideoSelection(api,endpoint.endpointId)}.onSuccess{latest->" in main
    assert "val latestIdentity=videoSelectionIdentity(latest)" in main
    assert "if(latestIdentity!=selectionIdentity)applyEndpointSelection(latest,true)" in main
    assert "LaunchedEffect(selectionIdentity,job,sources,isSeries)" in main

    video_screen = main.split("private fun VideoScreen", 1)[1]
    poll_body = re.search(
        r"LaunchedEffect\(api\.server,active,endpoint\.endpointId\)\{(?P<body>.*?)\n    \}",
        video_screen,
        re.DOTALL,
    ).group("body")
    assert "endpointVideoSelection(api,endpoint.endpointId)" in poll_body
    assert "loadJob(jobId)" not in poll_body
    assert "mediaHistoryPath" not in poll_body
    assert "/api/jobs/" not in poll_body
    assert "/api/video/external-links" not in poll_body
    assert "/api/video/monitor" not in poll_body
