"""Source-only regression checks for the Home poster rendering paths."""

import re
from pathlib import Path


MAIN = (
    Path(__file__).resolve().parents[2]
    / "android/home/app/src/main/java/com/skeleton/home/MainActivity.kt"
)


def composable(name: str) -> str:
    source = MAIN.read_text(encoding="utf-8")
    start = source.index(f"@Composable private fun {name}(")
    return re.split(r"\n@Composable private fun ", source[start:], maxsplit=1)[0]


def test_portrait_panel_fits_complete_artwork_and_preserves_layout_and_controls() -> None:
    panel = composable("VideoRemoteCard")
    assert "Image(bitmap,null,Modifier.fillMaxSize(),contentScale=ContentScale.Fit)" in panel
    assert "ContentScale.Crop" not in panel
    assert "val posterWidth=minOf(maxWidth,maxHeight*(2f/3f))" in panel
    assert "fillMaxHeight().width(posterWidth).clip(RoundedCornerShape(14.dp))" in panel
    assert "border(1.dp,Line,RoundedCornerShape(14.dp))" in panel
    assert "remoteBitmap(if(poster.isNotBlank()&&api.server!=null)api.server+poster else null,600,900)" in panel
    assert "step(-1)" in panel and "step(1)" in panel
    assert "SkeletonRange(seek,{seek=it},{onSeek(seek.toInt())}" in panel
    assert 'TransportRow(p.optBoolean("pause"),false,onControl,onMute)' in panel
    assert "VolumeRow(volume,onVolume,onMute)" in panel


def test_ambient_background_keeps_crop_blur_and_overscan() -> None:
    background = composable("AmbientPosterBackground")
    assert "Image(bitmap,null,Modifier.matchParentSize().scale(1.16f).blur(34.dp).alpha(.82f),contentScale=ContentScale.Crop)" in background
    assert "ContentScale.Fit" not in background


def test_youtube_landscape_preview_keeps_crop_and_widescreen_dimensions() -> None:
    panel = composable("YoutubeRemoteCard")
    assert "aspectRatio(16f/9f)" in panel
    assert "remoteBitmap(posterUrl,960,540)" in panel
    assert "Image(bitmap,null,Modifier.fillMaxSize(),contentScale=ContentScale.Crop)" in panel
    assert "ContentScale.Fit" not in panel
