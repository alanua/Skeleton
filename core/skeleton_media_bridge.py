from __future__ import annotations

import importlib
import importlib.util
from types import ModuleType

SKELETON_MEDIA_REPOSITORY = "alanua/skeleton-media"
SKELETON_MEDIA_COMPATIBILITY_SHA = "41d49e4a190e586bb3aa7b3cfca8db4b144e83b9"

_EXTERNAL_PREFIX = "skeleton_media"
_LOCAL_COMPAT_PREFIXES = {
    "home_edge.debian_media_bootstrap": "core.home_edge.debian_media_bootstrap",
    "home_edge.media_source_snapshot": "core.home_edge.media_source_snapshot",
    "video_understanding.models": "core.video_understanding.models",
    "video_understanding.runtime_install": "core.video_understanding.runtime_install",
}


class SkeletonMediaDependencyError(RuntimeError):
    pass


def external_package_available() -> bool:
    return importlib.util.find_spec(_EXTERNAL_PREFIX) is not None


def load_media_module(relative_name: str) -> ModuleType:
    if relative_name not in _LOCAL_COMPAT_PREFIXES:
        raise SkeletonMediaDependencyError(
            f"unsupported_skeleton_media_module:{relative_name}"
        )

    if external_package_available():
        module_name = f"{_EXTERNAL_PREFIX}.{relative_name}"
        try:
            return importlib.import_module(module_name)
        except Exception as exc:
            raise SkeletonMediaDependencyError(
                f"external_skeleton_media_import_failed:{relative_name}"
            ) from exc

    # Temporary compatibility path while the production/runtime installation
    # still comes from the Skeleton monorepo. New media implementation work
    # belongs in alanua/skeleton-media; these local copies are not canonical.
    return importlib.import_module(_LOCAL_COMPAT_PREFIXES[relative_name])


def dependency_source(relative_name: str) -> str:
    if relative_name not in _LOCAL_COMPAT_PREFIXES:
        raise SkeletonMediaDependencyError(
            f"unsupported_skeleton_media_module:{relative_name}"
        )
    return "external" if external_package_available() else "local_compatibility"
