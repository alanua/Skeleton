from __future__ import annotations

from core.home_edge import current_media_verifiers as verifiers


def gallery_manifest(**updates: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema_version": 8,
        "fit_policy": "landscape_screen_fit",
        "cadence_seconds": 900,
        "transition": "crossfade",
        "items": [
            {"file": f"gallery/work-{index:02d}.jpg", "width": 1920, "height": 1080}
            for index in range(1, 49)
        ],
    }
    manifest.update(updates)
    return manifest


def test_current_screensaver_contract_accepts_schema_v8_gallery_and_canvas_catalog() -> None:
    manifest = gallery_manifest()
    files = [item["file"] for item in manifest["items"]]  # type: ignore[index]

    receipt = verifiers.verify_current_screensaver_contract(
        manifest,
        verifiers.default_current_renderer_catalog(),
        existing_files=files,
        failed_units=(),
    )

    assert receipt["operation_id"] == verifiers.CURRENT_SCREENSAVER_VERIFY_OPERATION
    assert receipt["status"] == "healthy"
    assert receipt["gallery_schema_version"] == 8
    assert receipt["gallery_item_count"] == 48
    assert receipt["screen_fit_policy"] == "landscape_screen_fit"
    assert receipt["cadence_seconds"] == 900
    assert receipt["transition"] == "crossfade"
    assert receipt["renderer_count"] == 4
    assert receipt["renderer_ids"] == (
        "gallery,ambient_canvas_drift,ambient_canvas_lava,ambient_canvas_stars"
    )
    assert receipt["failed_unit_count"] == 0


def test_current_screensaver_contract_rejects_legacy_schema_and_missing_files() -> None:
    legacy = verifiers.verify_gallery_manifest(gallery_manifest(schema_version=5))
    missing_file = verifiers.verify_gallery_manifest(
        gallery_manifest(),
        existing_files={"gallery/work-01.jpg"},
    )

    assert legacy["gallery_status"] == "schema_version_mismatch"
    assert missing_file["gallery_status"] == "gallery_file_missing"


def test_current_renderer_catalog_excludes_legacy_xscreensaver_and_webgl_entries() -> None:
    catalog = verifiers.default_current_renderer_catalog()
    catalog["renderers"] = [
        *catalog["renderers"],  # type: ignore[operator]
        {"id": "debian_xscreensaver", "engine": "xscreensaver", "available": True},
    ]

    receipt = verifiers.verify_renderer_catalog(catalog)

    assert receipt["renderer_status"] == "excluded_renderer_present"
    assert "home-edge-screensaver-verify-v9" in (
        verifiers.SUPERSEDED_SCREENSAVER_VERIFY_OPERATIONS
    )
