from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


CURRENT_SCREENSAVER_VERIFY_OPERATION = "home_edge_01_screensaver_current_media_verify_v1"
CURRENT_SCREENSAVER_VERIFY_BIN = (
    "/home/oleksii/.local/bin/home-edge-screensaver-current-media-verify"
)
CURRENT_GALLERY_SCHEMA_VERSION = 8
CURRENT_GALLERY_ITEM_COUNT = 48
CURRENT_SCREEN_FIT_POLICY = "landscape_screen_fit"
CURRENT_CADENCE_SECONDS = 900
CURRENT_TRANSITION = "crossfade"
CURRENT_RENDERER_IDS = (
    "gallery",
    "ambient_canvas_drift",
    "ambient_canvas_lava",
    "ambient_canvas_stars",
)
SUPERSEDED_SCREENSAVER_VERIFY_OPERATIONS = (
    "home-edge-screensaver-verify-v6",
    "home-edge-screensaver-verify-v7",
    "home-edge-screensaver-verify-v8",
    "home-edge-screensaver-verify-v9",
    "home_edge_01_screensaver_verify_v6",
    "home_edge_01_screensaver_verify_v7",
    "home_edge_01_screensaver_verify_v8",
    "home_edge_01_screensaver_verify_v9",
)
EXCLUDED_RENDERER_FAMILIES = ("xscreensaver", "webgl")


def _schema_version(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        stripped = value.rsplit("v", 1)[-1]
        if stripped.isdigit():
            return int(stripped)
    return None


def _item_file(item: Mapping[str, Any]) -> str | None:
    for key in ("file", "path", "src", "relative_path"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _is_landscape(item: Mapping[str, Any]) -> bool:
    orientation = item.get("orientation")
    if orientation == "landscape":
        return True
    width = item.get("width")
    height = item.get("height")
    return (
        isinstance(width, int)
        and not isinstance(width, bool)
        and isinstance(height, int)
        and not isinstance(height, bool)
        and width > height > 0
    )


def verify_gallery_manifest(
    manifest: Mapping[str, Any],
    *,
    existing_files: Iterable[str] | None = None,
) -> dict[str, object]:
    file_set = set(existing_files) if existing_files is not None else None
    items = manifest.get("items") or manifest.get("gallery") or manifest.get("works")
    if not isinstance(items, list):
        return _gallery_result("missing_items", 0)

    files: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            return _gallery_result("item_invalid", len(items))
        file_name = _item_file(item)
        if file_name is None:
            return _gallery_result("item_file_missing", len(items))
        files.append(file_name)
        if not _is_landscape(item):
            return _gallery_result("non_landscape_item", len(items))
        policy = item.get("fit_policy", manifest.get("fit_policy"))
        if policy not in {None, CURRENT_SCREEN_FIT_POLICY, "screen_fit", "contain"}:
            return _gallery_result("screen_fit_policy_mismatch", len(items))

    if _schema_version(manifest.get("schema_version", manifest.get("schema"))) != CURRENT_GALLERY_SCHEMA_VERSION:
        return _gallery_result("schema_version_mismatch", len(items))
    if len(items) != CURRENT_GALLERY_ITEM_COUNT:
        return _gallery_result("item_count_mismatch", len(items))
    if file_set is not None and any(file_name not in file_set for file_name in files):
        return _gallery_result("gallery_file_missing", len(items))
    if manifest.get("cadence_seconds", CURRENT_CADENCE_SECONDS) != CURRENT_CADENCE_SECONDS:
        return _gallery_result("cadence_mismatch", len(items))
    if manifest.get("transition", CURRENT_TRANSITION) != CURRENT_TRANSITION:
        return _gallery_result("transition_mismatch", len(items))
    return _gallery_result("healthy", len(items))


def _gallery_result(status: str, count: int) -> dict[str, object]:
    return {
        "gallery_status": status,
        "gallery_schema_version": CURRENT_GALLERY_SCHEMA_VERSION,
        "gallery_item_count": count,
        "screen_fit_policy": CURRENT_SCREEN_FIT_POLICY,
        "cadence_seconds": CURRENT_CADENCE_SECONDS,
        "transition": CURRENT_TRANSITION,
    }


def verify_renderer_catalog(catalog: Mapping[str, Any]) -> dict[str, object]:
    raw_renderers = catalog.get("renderers")
    if not isinstance(raw_renderers, list):
        return _renderer_result("missing_renderers", ())
    renderer_ids: list[str] = []
    for renderer in raw_renderers:
        if not isinstance(renderer, Mapping):
            return _renderer_result("renderer_invalid", renderer_ids)
        renderer_id = renderer.get("id")
        engine = renderer.get("engine")
        if not isinstance(renderer_id, str) or not isinstance(engine, str):
            return _renderer_result("renderer_invalid", renderer_ids)
        if any(family in renderer_id.lower() or family in engine.lower() for family in EXCLUDED_RENDERER_FAMILIES):
            return _renderer_result("excluded_renderer_present", [*renderer_ids, renderer_id])
        renderer_ids.append(renderer_id)
    if tuple(renderer_ids) != CURRENT_RENDERER_IDS:
        return _renderer_result("renderer_catalog_mismatch", renderer_ids)
    if any(renderer.get("available") is False for renderer in raw_renderers if isinstance(renderer, Mapping)):
        return _renderer_result("renderer_unavailable", renderer_ids)
    return _renderer_result("healthy", renderer_ids)


def _renderer_result(status: str, renderer_ids: Iterable[str]) -> dict[str, object]:
    ids = tuple(renderer_ids)
    return {
        "renderer_status": status,
        "renderer_count": len(ids),
        "renderer_ids": ",".join(ids) or "none",
    }


def verify_current_screensaver_contract(
    gallery_manifest: Mapping[str, Any],
    renderer_catalog: Mapping[str, Any],
    *,
    existing_files: Iterable[str] | None = None,
    failed_units: Iterable[str] = (),
) -> dict[str, object]:
    gallery = verify_gallery_manifest(gallery_manifest, existing_files=existing_files)
    renderers = verify_renderer_catalog(renderer_catalog)
    failed = tuple(failed_units)
    status = (
        "healthy"
        if gallery["gallery_status"] == "healthy"
        and renderers["renderer_status"] == "healthy"
        and not failed
        else "degraded"
    )
    return {
        "operation_id": CURRENT_SCREENSAVER_VERIFY_OPERATION,
        "status": status,
        **gallery,
        **renderers,
        "failed_unit_count": len(failed),
    }


def default_current_renderer_catalog() -> dict[str, object]:
    return {
        "renderers": [
            {"id": "gallery", "engine": "image_gallery", "available": True},
            {"id": "ambient_canvas_drift", "engine": "canvas2d", "available": True},
            {"id": "ambient_canvas_lava", "engine": "canvas2d", "available": True},
            {"id": "ambient_canvas_stars", "engine": "canvas2d", "available": True},
        ]
    }


def receipt_status_lines(receipt: Mapping[str, object]) -> list[str]:
    keys = (
        "operation_id",
        "status",
        "gallery_schema_version",
        "gallery_item_count",
        "screen_fit_policy",
        "cadence_seconds",
        "transition",
        "renderer_status",
        "renderer_count",
        "renderer_ids",
        "failed_unit_count",
    )
    return [f"{key}={receipt[key]}" for key in keys]


def source_contract_receipt() -> dict[str, object]:
    manifest = {
        "schema_version": CURRENT_GALLERY_SCHEMA_VERSION,
        "fit_policy": CURRENT_SCREEN_FIT_POLICY,
        "cadence_seconds": CURRENT_CADENCE_SECONDS,
        "transition": CURRENT_TRANSITION,
        "items": [
            {"file": f"gallery/work-{index:02d}.jpg", "width": 1920, "height": 1080}
            for index in range(1, CURRENT_GALLERY_ITEM_COUNT + 1)
        ],
    }
    return verify_current_screensaver_contract(
        manifest,
        default_current_renderer_catalog(),
        existing_files=(_item["file"] for _item in manifest["items"]),
    )
