from core.runner_vnext_cutover_bridge import VNEXT_MODE_SHADOW


def test_vnext_mode_shadow_is_shadow() -> None:
    assert VNEXT_MODE_SHADOW == "shadow"
