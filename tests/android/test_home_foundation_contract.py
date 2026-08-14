import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANDROID_HOME = ROOT / "android" / "home"


def read(relative: str) -> str:
    return (ANDROID_HOME / relative).read_text(encoding="utf-8")


def test_app_display_name_is_home() -> None:
    assert "<string name=\"app_name\">Home</string>" in read("app/src/main/res/values/strings.xml")
    assert 'android:label="@string/app_name"' in read("app/src/main/AndroidManifest.xml")


def test_debug_preview_has_distinct_install_identity() -> None:
    gradle = read("app/build.gradle.kts")
    debug_strings = read("app/src/debug/res/values/strings.xml")
    assert 'applicationId = "com.skeleton.home"' in gradle
    assert 'applicationIdSuffix = ".preview"' in gradle
    assert 'versionNameSuffix = "-preview"' in gradle
    assert '<string name="app_name">Skeleton Home Preview</string>' in debug_strings


def test_native_compose_shell_without_webview() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in ANDROID_HOME.rglob("*.kt"))
    gradle = read("app/build.gradle.kts")
    assert "compose = true" in gradle
    assert "setContent" in source
    assert "WebView" not in source
    assert "android.webkit" not in source


def test_bottom_navigation_contract_and_remote_contextual_route() -> None:
    nav = read("app/src/main/java/com/skeleton/home/navigation/HomeRoutes.kt")
    assert 'val PrimaryBottomRoutes = listOf(\n    HomeRoute.Home,\n    HomeRoute.Video,\n    HomeRoute.Devices,\n)' in nav
    assert "fun bottomRoutesFor(" in nav
    assert "val OperatorBottomRoutes = PrimaryBottomRoutes + HomeRoute.OperatorHub" in nav
    assert "OperatorBottomRoutes" in nav
    assert "fun fromRoute(route: String): HomeRoute" in nav
    assert 'data object Remote : HomeRoute("remote", "Пульт")' in nav
    assert "HomeRoute.Remote" not in nav.split("val PrimaryBottomRoutes = listOf(", 1)[1].split(")", 1)[0]
    assert '"Головна"' in nav
    assert '"Відео"' in nav
    assert '"Пристрої"' in nav
    assert '"СК"' in nav


def test_operator_hub_bottom_navigation_and_authorization() -> None:
    auth = read("app/src/main/java/com/skeleton/home/auth/SyntheticSession.kt")
    routes = read("app/src/main/java/com/skeleton/home/navigation/HomeRoutes.kt")
    ui = read("app/src/main/java/com/skeleton/home/ui/HomeApp.kt")
    unit = read("app/src/test/java/com/skeleton/home/HomeContractTest.kt")
    android_test = read("app/src/androidTest/java/com/skeleton/home/HomeShellUiTest.kt")
    assert "session.role == UserRole.OPERATOR" in auth
    assert "fun operator()" in auth
    assert "fun ordinary()" in auth
    assert "fun spouse()" in auth
    assert "HomeRoute.OperatorHub -> auth.canAccessOperatorHub(session)" in routes
    assert "bottomRoutesFor(currentSession, session)" in ui
    assert "operator-hub-entry" not in ui
    assert 'contentDescription = "bottom-nav-${route.route}"' in ui
    assert "Icons.Filled.Hub" not in ui
    assert "MaterialHubIcon" in ui
    assert 'name = "MaterialHub"' in ui
    assert "HomeRoute.OperatorHub -> MaterialHubIcon" in ui
    assert "rememberSaveable { mutableStateOf(initialRoute.route) }" in ui
    assert "HomeRoute.fromRoute(currentRouteId)" in ui
    assert 'data object OperatorHub : HomeRoute("operator-hub", "СК")' in routes
    assert 'listOf("Головна", "Відео", "Пристрої", "СК")' in unit
    assert "directOperatorHubAuthorizationFailsClosedForNonOperators" in unit
    assert "spouseDirectOperatorHubRouteIsDenied" in android_test
    assert "Доступ до розділу відхилено" in ui


def test_home_seek_15_controls_use_local_dependency_free_icons() -> None:
    ui = read("app/src/main/java/com/skeleton/home/ui/HomeApp.kt")
    unavailable_icons = ["Forward" + "15", "Replay" + "15"]
    for icon in unavailable_icons:
        assert f"Icons.Filled.{icon}" not in ui
        assert f"androidx.compose.material.icons.filled.{icon}" not in ui
    assert 'contentDescription = "control-back-15"' in ui
    assert 'contentDescription = "control-forward-15"' in ui
    assert 'name = "SeekBack15"' in ui
    assert 'name = "SeekForward15"' in ui
    assert 'label = "15 с"' in ui


def test_future_interfaces_and_state_values_exist() -> None:
    contracts = read("app/src/main/java/com/skeleton/home/domain/HomeContracts.kt")
    for name in [
        "interface CanonicalHomeApi",
        "interface AuthSessionProvider",
        "interface ConnectivityMonitor",
        "interface SecureStorage",
        "interface VerifiedActionStateStore",
    ]:
        assert name in contracts
    for value in ["ONLINE", "DEGRADED", "OFFLINE", "SENT", "ACCEPTED", "APPLIED", "PHYSICALLY_VERIFIED"]:
        assert value in contracts


def test_no_endpoint_secret_or_live_fixture_values() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in ANDROID_HOME.rglob("*")
        if path.is_file() and path.suffix in {".kt", ".kts", ".xml", ".md"}
    )
    urls = re.findall(r"https?://[^\"]+", text)
    assert urls == ["http://schemas.android.com/apk/res/android"]
    forbidden = ["api_key", "apikey", "secret", "token", "hmac", "ssh", "device_id"]
    lowered = text.lower()
    for word in forbidden:
        assert word not in lowered
