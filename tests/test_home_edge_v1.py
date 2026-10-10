from core.home_edge_v1 import (
    CapabilityDomain,
    HomeEdgeRouter,
    HomeEdgeTask,
    PolicyDecision,
    RiskLevel,
    default_executors,
    home_edge_v1_bootstrap_registry,
)
from core.exo_device_control_plane import (
    CapabilityDomain as ExoCapabilityDomain,
    ExoCapability,
    ExoDevice,
    ExoDeviceRegistry,
    StableDeviceIdentity,
    canonical_exo_registry,
)


HOME_EDGE_ID = "home_edge_01"
SAMSUNG_ID = "samsung_tv"
SHARP_ID = "sharp_tv_living_room"


def test_role_is_portable() -> None:
    node = home_edge_v1_bootstrap_registry().nodes[0]
    assert node.node_id == "home-edge-role"
    assert node.current_host == "home-edge-01"
    assert node.portable_role is True


def test_bootstrap_registry_includes_migrated_exo_device_identities() -> None:
    registry = home_edge_v1_bootstrap_registry()
    devices = {device.device_id: device for device in registry.devices}

    assert {HOME_EDGE_ID, SAMSUNG_ID, SHARP_ID} <= set(devices)
    assert devices[HOME_EDGE_ID].device_type == "home_edge"
    assert devices[SAMSUNG_ID].device_type == "samsung_adb"
    assert devices[SHARP_ID].device_type == "sharp_ir"
    assert devices[SAMSUNG_ID].capability("adb.watchdog") is not None
    assert devices[SHARP_ID].capability("ir.transmit") is not None
    assert devices[SHARP_ID].capability("ir.transmit").risk == RiskLevel.YELLOW


def test_bootstrap_registry_is_driven_by_supplied_exo_registry() -> None:
    registry = ExoDeviceRegistry(
        devices=(
            *canonical_exo_registry().devices,
            ExoDevice(
                identity=StableDeviceIdentity(
                    device_id="printer.brother_mfp",
                    adapter_kind="home_edge",
                    vendor="Brother",
                    model="MFP service endpoint",
                    location="office",
                ),
                capabilities=(ExoCapability("service.file_status", ExoCapabilityDomain.SERVICE_HUB),),
                transport_ids=("home_edge_exec_gateway",),
            ),
        )
    )

    devices = {device.device_id: device for device in home_edge_v1_bootstrap_registry(registry).devices}

    assert "printer.brother_mfp" in devices
    assert devices["printer.brother_mfp"].device_type == "home_edge"
    assert devices["printer.brother_mfp"].capability("service.file_status") is not None


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


def test_targeted_exo_device_capability_routes_through_portable_gateway() -> None:
    router = HomeEdgeRouter(home_edge_v1_bootstrap_registry())
    task = HomeEdgeTask(task_type="adb.watchdog", capability="adb.watchdog", target=SAMSUNG_ID)
    result, audit = router.route(task)
    assert result is not None
    assert result["node_id"] == "home-edge-role"
    assert result["target"] == SAMSUNG_ID
    assert audit.status == "ok"
    assert audit.domain == CapabilityDomain.PROVISIONING_RECOVERY


def test_targeted_device_missing_capability_fails_closed() -> None:
    router = HomeEdgeRouter(home_edge_v1_bootstrap_registry())
    task = HomeEdgeTask(task_type="adb.watchdog", capability="adb.watchdog", target=SHARP_ID)
    result, audit = router.route(task)
    assert result is None
    assert audit.status == "blocked"
    assert audit.message == "no registry target with requested capability"


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
