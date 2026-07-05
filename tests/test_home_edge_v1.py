from core.home_edge_v1 import HomeEdgeTask, HomeEdgeRouter, PolicyDecision, RiskLevel, home_edge_v1_bootstrap_registry


def test_role_is_portable() -> None:
    node = home_edge_v1_bootstrap_registry().nodes[0]
    assert node.node_id == "home-edge-role"
    assert node.current_host == "home-edge-01"
    assert node.portable_role is True


def test_green_task_routes_in_dry_run() -> None:
    router = HomeEdgeRouter(home_edge_v1_bootstrap_registry())
    task = HomeEdgeTask(task_type="media.play", capability="media.play", target="living_room", risk=RiskLevel.GREEN)
    result, audit = router.route(task)
    assert result is not None
    assert result["status"] == "dry_run"
    assert result["node_id"] == "home-edge-role"
    assert audit.status == "ok"
    assert audit.decision == PolicyDecision.ALLOW


def test_yellow_task_waits_for_approval() -> None:
    router = HomeEdgeRouter(home_edge_v1_bootstrap_registry())
    task = HomeEdgeTask(task_type="network.change", capability="network.change", target="router", risk=RiskLevel.YELLOW)
    result, audit = router.route(task)
    assert result is None
    assert audit.status == "blocked"
    assert audit.decision == PolicyDecision.NEED_APPROVAL


def test_missing_capability_is_not_routed() -> None:
    router = HomeEdgeRouter(home_edge_v1_bootstrap_registry())
    task = HomeEdgeTask(task_type="unknown", capability="missing.capability", target="unknown")
    result, audit = router.route(task)
    assert result is None
    assert audit.status == "blocked"
