from __future__ import annotations

import math
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MAIN_ACTIVITY = ROOT / "android/home/app/src/main/java/com/skeleton/home/MainActivity.kt"
DEFAULT_PORTRAIT_ASPECT = 2 / 3


def _source() -> str:
    return MAIN_ACTIVITY.read_text(encoding="utf-8")


def _portrait_aspect(width: int | None, height: int | None) -> float:
    if width is None or height is None or width <= 0 or height <= 0:
        return DEFAULT_PORTRAIT_ASPECT
    aspect = width / height
    return aspect if math.isfinite(aspect) and 0.4 <= aspect <= 0.85 else DEFAULT_PORTRAIT_ASPECT


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (500, 750, 2 / 3),
        (600, 900, 2 / 3),
        (400, 800, 1 / 2),
        (720, 960, 3 / 4),
        (400, 1000, 0.4),
        (850, 1000, 0.85),
    ],
)
def test_portrait_aspect_contract_accepts_valid_bitmap_dimensions(
    width: int, height: int, expected: float
) -> None:
    assert _portrait_aspect(width, height) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("width", "height"),
    [
        (None, None),
        (None, 900),
        (600, None),
        (0, 900),
        (600, 0),
        (-1, 900),
        (1, 1000),
        (399, 1000),
        (851, 1000),
        (1000, 1000),
        (900, 600),
    ],
)
def test_portrait_aspect_contract_defaults_for_missing_or_invalid_dimensions(
    width: int | None, height: int | None
) -> None:
    assert _portrait_aspect(width, height) == pytest.approx(DEFAULT_PORTRAIT_ASPECT)


def test_kotlin_helper_implements_the_portrait_aspect_contract() -> None:
    source = _source()
    helper = re.search(
        r"private fun portraitAspect\(width:Int\?,height:Int\?\):Float\{(?P<body>.*?)\n\}",
        source,
        re.DOTALL,
    )

    assert "private const val DefaultPortraitAspect = 2f / 3f" in source
    assert helper is not None
    body = helper.group("body")
    assert "width==null||height==null||width<=0||height<=0" in body
    assert "width.toFloat()/height.toFloat()" in body
    assert "it.isFinite()&&it>=0.4f&&it<=0.85f" in body
    assert body.count("DefaultPortraitAspect") == 2


def test_video_foreground_frame_fits_both_bounds_and_preserves_bitmap_aspect() -> None:
    source = _source()
    card = source.split("@Composable private fun VideoRemoteCard", 1)[1].split(
        "@Composable private fun TvRemoteCard", 1
    )[0]

    assert "val posterAspect=portraitAspect(bitmap?.width,bitmap?.height)" in card
    assert "val posterWidth=minOf(maxWidth,maxHeight*posterAspect)" in card
    assert "val posterHeight=posterWidth/posterAspect" in card
    assert "Modifier.width(posterWidth).height(posterHeight)" in card


def test_video_foreground_frame_fits_image_without_crop_or_fill_max_height() -> None:
    source = _source()
    card = source.split("@Composable private fun VideoRemoteCard", 1)[1].split(
        "@Composable private fun TvRemoteCard", 1
    )[0]
    foreground = card.split("val frameSide=", 1)[1].split("if(seriesNav)", 1)[0]

    assert "contentScale=ContentScale.Fit" in foreground
    assert "ContentScale.Crop" not in foreground
    assert "fillMaxHeight()" not in foreground
