from __future__ import annotations

import importlib
import types

import pytest

from core import skeleton_media_bridge as bridge


def test_supported_modules_are_explicit_and_canonical_repo_is_recorded() -> None:
    assert bridge.SKELETON_MEDIA_REPOSITORY == "alanua/skeleton-media"
    assert bridge.SKELETON_MEDIA_COMPATIBILITY_SHA == "41d49e4a190e586bb3aa7b3cfca8db4b144e83b9"
    assert bridge.dependency_source("home_edge.media_source_snapshot") in {
        "external",
        "local_compatibility",
    }


def test_missing_external_package_uses_local_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = types.ModuleType("sentinel")
    monkeypatch.setattr(bridge, "external_package_available", lambda: False)
    calls: list[str] = []

    def fake_import(name: str):
        calls.append(name)
        return sentinel

    monkeypatch.setattr(importlib, "import_module", fake_import)

    assert bridge.load_media_module("home_edge.debian_media_bootstrap") is sentinel
    assert calls == ["core.home_edge.debian_media_bootstrap"]


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


def test_broken_external_package_fails_closed_without_local_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge, "external_package_available", lambda: True)
    calls: list[str] = []

    def fake_import(name: str):
        calls.append(name)
        raise ImportError("broken external package")

    monkeypatch.setattr(importlib, "import_module", fake_import)

    with pytest.raises(
        bridge.SkeletonMediaDependencyError,
        match="external_skeleton_media_import_failed",
    ):
        bridge.load_media_module("video_understanding.runtime_install")

    assert calls == ["skeleton_media.video_understanding.runtime_install"]


def test_unknown_module_is_rejected() -> None:
    with pytest.raises(
        bridge.SkeletonMediaDependencyError,
        match="unsupported_skeleton_media_module",
    ):
        bridge.load_media_module("unknown.module")
