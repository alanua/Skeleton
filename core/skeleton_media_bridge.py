from __future__ import annotations

import importlib
import importlib.util
from types import ModuleType

SKELETON_MEDIA_REPOSITORY = "alanua/skeleton-media"
SKELETON_MEDIA_REQUIRED_SHA = "7089b1ba37292aab0283cadb8ac85644cc3d28f8"

_EXTERNAL_PREFIX = "skeleton_media"
_SUPPORTED_MODULES = frozenset(
    {
        "home_edge.debian_media_bootstrap",
        "home_edge.media_source_snapshot",
        "video_understanding.runtime_install",
    }
)


class SkeletonMediaDependencyError(RuntimeError):
    pass


def external_package_available() -> bool:
    return importlib.util.find_spec(_EXTERNAL_PREFIX) is not None


def load_media_module(relative_name: str) -> ModuleType:
    if relative_name not in _SUPPORTED_MODULES:
        raise SkeletonMediaDependencyError(
            f"unsupported_skeleton_media_module:{relative_name}"
        )
    if not external_package_available():
        raise SkeletonMediaDependencyError(
            f"skeleton_media_dependency_unavailable:{relative_name}"
        )
    module_name = f"{_EXTERNAL_PREFIX}.{relative_name}"
    try:
        return importlib.import_module(module_name)
    except Exception as exc:
        raise SkeletonMediaDependencyError(
            f"external_skeleton_media_import_failed:{relative_name}"
        ) from exc


def dependency_source(relative_name: str) -> str:
    if relative_name not in _SUPPORTED_MODULES:
        raise SkeletonMediaDependencyError(
            f"unsupported_skeleton_media_module:{relative_name}"
        )
    return "external" if external_package_available() else "unavailable"
