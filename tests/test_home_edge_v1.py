from core.home_edge_v1 import (
    CapabilityDomain,
    HomeEdgeRouter,
    HomeEdgeTask,
    PolicyDecision,
    RiskLevel,
    default_executors,
    home_edge_v1_bootstrap_registry,
)


def test_role_is_portable() -> None:
    node = home_edge_v1_bootstrap_registry().nodes[0]
    assert node.node_id == "home-edge-role"
    assert node.current_host == "home-edge-01"
    assert node.portable_role is True


def test_all_domains_have_executor_stubs() -> None:
    assert set(default_executors()) == set(CapabilityDomain)


def test_green_task_routes_in_dry_run() -> None:
    router = HomeEdgeRouter(home_edge_v1_bootstrap_registry())
    task = HomeEdgeTask(task_type="media.play", capability="media.play", target="living_room")
    result, audit = router.route(task)
    assert result is not None
    assert result["status"] == "dry_run"
    assert result["node_id"] == "home-edge-role"
    assert result["domain"] == CapabilityDomain.MEDIA_PRESENCE.value
    assert audit.status == "ok"
    assert audit.decision == PolicyDecision.ALLOW
    assert audit.domain == CapabilityDomain.MEDIA_PRESENCE


def test_yellow_task_waits_for_approval() -> None:
    router = HomeEdgeRouter(home_edge_v1_bootstrap_registry())
    task = HomeEdgeTask(task_type="network.change", capability="network.observe", target="router", risk=RiskLevel.YELLOW)
    result, audit = router.route(task)
    assert result is None
    assert audit.status == "blocked"
    assert audit.decision == PolicyDecision.NEED_APPROVAL


def test_live_mode_waits_for_approval_even_when_green() -> None:
    router = HomeEdgeRouter(home_edge_v1_bootstrap_registry())
    task = HomeEdgeTask(task_type="media.play", capability="media.play", target="living_room", dry_run=False)
    result, audit = router.route(task)
    assert result is None
    assert audit.decision == PolicyDecision.NEED_APPROVAL


def test_missing_capability_is_not_routed() -> None:
    router = HomeEdgeRouter(home_edge_v1_bootstrap_registry())
    task = HomeEdgeTask(task_type="unknown", capability="missing.capability", target="unknown")
    result, audit = router.route(task)
    assert result is None
    assert audit.status == "blocked"
