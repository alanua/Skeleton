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
