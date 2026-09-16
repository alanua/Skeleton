from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANDROID_HOME = ROOT / "android" / "home"


def read(relative: str) -> str:
    return (ANDROID_HOME / relative).read_text(encoding="utf-8")


def test_sharp_remote_is_contextual_and_reuses_home_edge() -> None:
    nav = read("app/src/main/java/com/skeleton/home/navigation/HomeRoutes.kt")
    ui = read("app/src/main/java/com/skeleton/home/ui/HomeApp.kt")
    remote = read("app/src/main/java/com/skeleton/home/ui/SharpRemoteScreen.kt")
    api = read("app/src/main/java/com/skeleton/home/homeedge/SharpManagedDisplayHomeEdgeApi.kt")

    assert 'data object Remote : HomeRoute("remote", "Пульт")' in nav
    assert "HomeRoute.Remote" not in nav.split("val PrimaryBottomRoutes = listOf(", 1)[1].split(")", 1)[0]
    assert "HomeRoute.Devices -> DevicesScreen" in ui
    assert "HomeRoute.Remote -> SharpRemoteScreen" in ui
    assert 'Text("Пульт ТВ"' in remote
    assert 'Text("Sharp TV"' in remote
    assert "BuildConfig.HOME_EDGE_BASE_URLS" in api
    assert "java.net.HttpURLConnection" in api


def test_sharp_remote_has_only_named_bounded_controls() -> None:
    remote = read("app/src/main/java/com/skeleton/home/ui/SharpRemoteScreen.kt")
    api = read("app/src/main/java/com/skeleton/home/homeedge/SharpManagedDisplayHomeEdgeApi.kt")

    for marker in [
        "sharp-tv-hdmi1",
        "sharp-tv-hdmi2",
        "sharp-tv-key-up",
        "sharp-tv-key-down",
        "sharp-tv-key-left",
        "sharp-tv-key-right",
        "sharp-tv-key-ok",
        "sharp-tv-key-back",
        "sharp-tv-key-menu",
        "sharp-tv-standby",
    ]:
        assert marker in remote

    assert "supportsHdmi3" in remote
    assert "powerOnAvailable" in remote
    assert "power_on_physically_verified" in api
    for forbidden in ["raw_key", "keycode", "volume_up", "volume_down", "native_youtube", "factory"]:
        assert forbidden not in (remote + api).lower()


def test_sharp_remote_fails_closed_when_display_is_unreachable() -> None:
    remote = read("app/src/main/java/com/skeleton/home/ui/SharpRemoteScreen.kt")
    assert "val reachable = status?.reachable == true" in remote
    assert "enabled = reachable" in remote
    assert 'statusMessage = "Sharp TV недоступний"' in remote


def test_sharp_remote_preserves_verified_action_states() -> None:
    api = read("app/src/main/java/com/skeleton/home/homeedge/SharpManagedDisplayHomeEdgeApi.kt")
    remote = read("app/src/main/java/com/skeleton/home/ui/SharpRemoteScreen.kt")
    for state in ["SENT", "ACCEPTED", "APPLIED", "PHYSICALLY_VERIFIED"]:
        assert f"VerifiedActionState.{state}" in api
        assert f"VerifiedActionState.{state}" in remote
