from __future__ import annotations

import importlib
import types

import pytest

from core import skeleton_media_bridge as bridge


def test_canonical_media_repository_and_required_sha_are_explicit() -> None:
    assert bridge.SKELETON_MEDIA_REPOSITORY == "alanua/skeleton-media"
    assert bridge.SKELETON_MEDIA_REQUIRED_SHA == "7089b1ba37292aab0283cadb8ac85644cc3d28f8"


def test_missing_external_package_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "external_package_available", lambda: False)
    with pytest.raises(
        bridge.SkeletonMediaDependencyError,
        match="skeleton_media_dependency_unavailable",
    ):
        bridge.load_media_module("home_edge.debian_media_bootstrap")
    assert bridge.dependency_source("home_edge.media_source_snapshot") == "unavailable"


def test_installed_external_package_is_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = types.ModuleType("sentinel")
    monkeypatch.setattr(bridge, "external_package_available", lambda: True)
    calls: list[str] = []

    def fake_import(name: str):
        calls.append(name)
        return sentinel

    monkeypatch.setattr(importlib, "import_module", fake_import)
    assert bridge.load_media_module("home_edge.media_source_snapshot") is sentinel
    assert calls == ["skeleton_media.home_edge.media_source_snapshot"]
    assert bridge.dependency_source("home_edge.media_source_snapshot") == "external"


def test_broken_external_package_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "external_package_available", lambda: True)

    def fake_import(_name: str):
        raise ImportError("broken external package")

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(
        bridge.SkeletonMediaDependencyError,
        match="external_skeleton_media_import_failed",
    ):
        bridge.load_media_module("video_understanding.runtime_install")


def test_unknown_module_is_rejected() -> None:
    with pytest.raises(
        bridge.SkeletonMediaDependencyError,
        match="unsupported_skeleton_media_module",
    ):
        bridge.load_media_module("unknown.module")


def test_canonical_home_media_endpoints_are_capability_driven() -> None:
    payload = bridge.canonical_home_media_endpoints()

    assert payload["schema"] == "skeleton.home.media_endpoints.v1"
    endpoints = {item["endpoint_id"]: item for item in payload["endpoints"]}
    assert set(endpoints) == {"home_edge_tv", "samsung_tv"}
    assert endpoints["home_edge_tv"]["adapter_kind"] == "home_edge_tv"
    assert endpoints["samsung_tv"]["adapter_kind"] == "samsung"
    assert "handoff" in endpoints["home_edge_tv"]["capabilities"]
    assert "handoff" in endpoints["samsung_tv"]["capabilities"]


def test_mutations_require_explicit_endpoint_id_and_keep_state_isolated() -> None:
    registry = bridge.canonical_home_media_registry()

    registry.mutate("home_edge_tv", bridge.MediaEndpointCapability.VOLUME, {"level": 35})
    registry.mutate(
        "home_edge_tv",
        bridge.MediaEndpointCapability.HISTORY,
        {"history_id": "tv-work", "title": "TV work"},
    )
    registry.mutate("samsung_tv", bridge.MediaEndpointCapability.VOLUME, {"level": 11})
    registry.mutate(
        "samsung_tv",
        bridge.MediaEndpointCapability.PLAY,
        {"job_id": "samsung-job", "source_id": "smarttube"},
    )

    tv = registry.snapshot("home_edge_tv")
    samsung = registry.snapshot("samsung_tv")
    assert tv.volume == 35
    assert samsung.volume == 11
    assert [item["history_id"] for item in tv.history] == ["tv-work"]
    assert samsung.history == []
    assert samsung.session["job_id"] == "samsung-job"
    assert tv.session == {}
    with pytest.raises(bridge.MediaEndpointError, match="endpoint_id_required"):
        registry.mutate("", bridge.MediaEndpointCapability.VOLUME, {"level": 1})
    with pytest.raises(bridge.MediaEndpointError, match="unknown_endpoint_id"):
        registry.mutate("global", bridge.MediaEndpointCapability.VOLUME, {"level": 1})


def test_generic_copy_and_move_handoff_use_endpoint_ids() -> None:
    registry = bridge.canonical_home_media_registry()
    registry.mutate(
        "home_edge_tv",
        bridge.MediaEndpointCapability.PLAY,
        {"job_id": "job-1", "source_id": "src-1", "display-title": "Film"},
    )
    registry.mutate(
        "home_edge_tv",
        bridge.MediaEndpointCapability.HISTORY,
        {"history_id": "hist-1"},
    )

    copied = registry.handoff(
        source_endpoint_id="home_edge_tv",
        destination_endpoint_id="samsung_tv",
    )
    assert copied["source_endpoint_id"] == "home_edge_tv"
    assert copied["destination_endpoint_id"] == "samsung_tv"
    assert copied["move"] is False
    assert registry.snapshot("samsung_tv").session["last_handoff"]["player"]["job_id"] == "job-1"
    assert registry.snapshot("home_edge_tv").session["job_id"] == "job-1"

    moved = registry.handoff(
        source_endpoint_id="samsung_tv",
        destination_endpoint_id="home_edge_tv",
        move=True,
    )
    assert moved["move"] is True
    assert registry.snapshot("samsung_tv").session["last_handoff"]["cleared"] is True


def test_synthetic_third_endpoint_adapter_extends_without_tv_samsung_coupling() -> None:
    projector = bridge.InMemoryCapabilityAdapter(
        bridge.MediaEndpoint(
            endpoint_id="projector_den",
            label="Projector",
            adapter_kind="synthetic_projector",
            icon="television-play",
            capabilities=(
                bridge.MediaEndpointCapability.PLAYER_STATUS,
                bridge.MediaEndpointCapability.PLAY,
                bridge.MediaEndpointCapability.VOLUME,
                bridge.MediaEndpointCapability.HANDOFF,
            ),
        )
    )
    registry = bridge.canonical_home_media_registry(extra_adapters=(projector,))

    registry.mutate("projector_den", bridge.MediaEndpointCapability.VOLUME, {"level": 66})
    registry.mutate("home_edge_tv", bridge.MediaEndpointCapability.VOLUME, {"level": 20})
    registry.mutate("samsung_tv", bridge.MediaEndpointCapability.VOLUME, {"level": 30})

    assert [endpoint.endpoint_id for endpoint in registry.endpoints] == [
        "home_edge_tv",
        "samsung_tv",
        "projector_den",
    ]
    assert registry.snapshot("projector_den").volume == 66
    assert registry.snapshot("home_edge_tv").volume == 20
    assert registry.snapshot("samsung_tv").volume == 30
    with pytest.raises(bridge.MediaEndpointError, match="endpoint_capability_unsupported"):
        registry.mutate("projector_den", bridge.MediaEndpointCapability.TV_CHANNELS, {})


def test_skeleton_core_contains_no_media_implementation_copies() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    forbidden = (
        root / "core/video_understanding",
        root / "ops/skeleton_cast",
        root / "core/home_edge/debian_media_bootstrap.py",
        root / "core/home_edge/media_source_snapshot.py",
        root / "core/home_edge/media_display_ownership.py",
        root / "core/home_edge/adaptive_remote.py",
        root / ".github/workflows/video-understanding-runtime-launch.yml",
    )
    assert all(not path.exists() for path in forbidden)
