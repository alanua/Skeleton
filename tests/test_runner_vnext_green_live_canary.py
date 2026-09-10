from core.runner_vnext_cutover_bridge import VNEXT_MODE_GREEN_CANARY


def test_runner_vnext_green_live_canary_mode_constant() -> None:
    assert VNEXT_MODE_GREEN_CANARY == "green_canary"
