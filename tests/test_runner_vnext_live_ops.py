from __future__ import annotations

import core.runner_vnext_live_ops as live_ops


def test_live_ops_namespace_is_effect_free() -> None:
    assert live_ops.__name__ == "core.runner_vnext_live_ops"
