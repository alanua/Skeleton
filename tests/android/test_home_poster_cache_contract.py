"""Source contracts for the Android request lifecycle; no network or devices."""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[2] / "android/home/app/src/main/java/com/skeleton/home/MainActivity.kt").read_text()


def test_request_state_is_reset_before_loading_new_title_or_endpoint():
    body = SOURCE.split("@Composable private fun remoteBitmap(", 1)[1].split("private fun drawableBitmap", 1)[0]
    assert "return androidx.compose.runtime.key(key){" in body
    assert "val initial=remember{if(key.isBlank())null else remoteBitmapCache.get(key)}" in body
    assert body.index("remoteBitmapCache.get(key)") < body.index("withContext(Dispatchers.IO)")
    assert "if(loaded!=null)value=loaded" in body


def test_round_trip_uses_existing_bounded_cache_and_serialized_decode():
    body = SOURCE.split("private fun loadRemoteBitmap(", 1)[1].split("@Composable private fun remoteBitmap", 1)[0]
    assert body.index("remoteBitmapCache.get(key)") < body.index("file.readBytes()")
    assert body.index("file.readBytes()") < body.index("URL(url)")
    assert "?.also{remoteBitmapCache.put(key,it)}" in body
    assert "Array(32){Any()}" in SOURCE
    assert "48 * 1024" in SOURCE
    assert "remoteBitmapLocks.remove" not in SOURCE


def test_cache_miss_and_temporary_failure_do_not_poison_ram_cache():
    assert "catch(_:Exception){null}" in SOURCE
    assert "if(bytes.isEmpty())" in SOURCE
    assert "if(loaded!=null)value=loaded" in SOURCE
    assert "decodeRemoteBitmap(bytes,maxWidth,maxHeight)?.also" in SOURCE


def test_unknown_canonical_video_does_not_authorize_stale_poster():
    assert "val sameVideo=remoteVideoId.isBlank()||(canonicalVideoId.isNotBlank()&&remoteVideoId==canonicalVideoId)" in SOURCE
    assert 'remotePoster.ifBlank{if(sameVideo)canonicalPoster else ""}' in SOURCE


def test_mode_and_tab_surfaces_remain_persistent():
    assert "PersistentTabSlot(tab==0)" in SOURCE
    for panel in range(3):
        assert f"PersistentTabSlot(panel=={panel})" in SOURCE
