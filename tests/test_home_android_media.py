from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_ACTIVITY = ROOT / "android/home/app/src/main/java/com/skeleton/home/MainActivity.kt"
BUILD_GRADLE = ROOT / "android/home/app/build.gradle.kts"


def _source() -> str:
    return MAIN_ACTIVITY.read_text(encoding="utf-8")


def test_home_release_metadata_is_1_4_32_148() -> None:
    text = BUILD_GRADLE.read_text(encoding="utf-8")

    assert "versionCode = 148" in text
    assert 'versionName = "1.4.32"' in text


def test_home_media_volume_and_mute_are_endpoint_isolated() -> None:
    text = _source()

    assert "val endpointVolumes=remember{mutableStateMapOf<String,Int>()}" in text
    assert "val endpointLastNonzero=remember{mutableStateMapOf<String,Int>()}" in text
    assert "val endpointVolumeReady=remember{mutableStateMapOf<String,Boolean>()}" in text
    assert "var volume by remember(endpoint.endpointId)" in text
    assert "var lastNonzero by remember(endpoint.endpointId)" in text
    assert "var volumeReady by remember(endpoint.endpointId)" in text
    assert "endpointVolumes[endpoint.endpointId]=clamped" in text
    assert "endpointLastNonzero[endpoint.endpointId]=clamped" in text
    assert "endpointVolumeReady[endpoint.endpointId]=true" in text
    assert 'onVolume={v->if(volumeReady){setEndpointVolume(v);scope.launch{runCatching{api.post(endpointPath(endpoint.endpointId,"/volume"),endpointMutation(endpoint.endpointId,"level" to v.coerceIn(0,100)))}}}}' in text
    assert 'onMute={if(volumeReady)scope.launch{val v=if(volume==0)lastNonzero.coerceAtLeast(1) else 0;setEndpointVolume(v);runCatching{api.post(endpointPath(endpoint.endpointId,"/volume"),endpointMutation(endpoint.endpointId,"level" to v))};refresh()}}' in text
    assert 'api.post(endpointPath(endpoint.endpointId,"/volume"),endpointMutation(endpoint.endpointId,"level" to v))' in text


def test_home_tv_youtube_uses_endpoint_player_without_snapshot_polling() -> None:
    text = _source()

    assert 'api.get(endpointPath(endpoint.endpointId,"/snapshot"))' not in text
    assert '"/snapshot"' not in text
    assert 'api.get(endpointPath(endpoint.endpointId,"/player"))' in text
    assert "parseMediaEndpointPlayerState" in text
    assert "val player=j" in text
    assert 'lastGoodPlayer.optString("display-title",lastGoodPlayer.optString("title",lastGoodPlayer.optString("media-title","Відео")))' in text
    assert 'lastGoodPlayer.optString("poster_landscape",lastGoodPlayer.optString("poster",""))' in text
    assert 'val videoId=lastGoodPlayer.optString("video_id","")' in text
    assert 'val serverPos=lastGoodPlayer.optDouble("time-pos",0.0).toFloat()' in text
    assert 'val rawDuration=lastGoodPlayer.optDouble("duration",0.0).toFloat()' in text
    assert 'val paused=lastGoodPlayer.optBoolean("pause",false)' in text
    assert 'runCatching{api.get(endpointPath(endpoint.endpointId,"/remote/status"))}' in text
    assert 'runCatching{api.get(endpointPath(endpointId,"/remote/status"))}' not in text
    assert "delay(if(endpoint.adapterKind!=\"samsung\"&&mode==\"kiosk\")850 else 2500)" in text
    assert "delay(4000)" in text
    assert "val youtubeControls=remote.optJSONObject" in text
    assert "LaunchedEffect(videoId,running,canSeek,paused,rawDuration)" in text
    card = text.split("@Composable private fun YoutubeRemoteCard", 1)[1].split("@Composable private fun VideoRemoteCard", 1)[0]
    assert "api.get(" not in card


def test_home_video_selection_and_history_are_canonical_endpoint_scoped() -> None:
    text = _source()

    assert 'endpointVideoSelection(api,endpoint.endpointId)' in text
    assert 'api.get("/api/video/selection")' not in text
    assert 'api.put(endpointPath(endpoint.endpointId,"/video/selection"),selBody)' in text
    assert 'api.put("/api/video/selection",selBody)' not in text
    assert 'private fun mediaHistoryPath(endpointId:String):String="/api/media/history?endpoint=${Uri.encode(endpointId)}"' in text
    assert 'private fun historyKey(item:JSONObject):String=item.cleanText("history_key",item.cleanText("content_key"))' in text
    assert 'private fun mediaHistoryDeletePath(endpointId:String,item:JSONObject):String="/api/media/history/${Uri.encode(historyKey(item))}?endpoint=${Uri.encode(endpointId)}"' in text
    assert "canonicalHistoryItems(api.get(mediaHistoryPath(endpointId)).optJSONArray" in text
    assert "canonicalHistoryItems(it.optJSONArray" in text
    assert "historyEpisodeLabel(item)" in text
    assert 'api.get(mediaHistoryPath(endpointId))' in text
    assert 'api.get(mediaHistoryPath(endpoint.endpointId))' in text
    assert 'api.delete(mediaHistoryDeletePath(endpointId,item))' in text
    assert 'val preferred=historyPreferredSelection(endpoint.endpointId,item)' in text
    assert 'runCatching{api.put(endpointPath(endpoint.endpointId,"/video/selection"),preferred)}' in text
    assert 'selection=preferred' in text
    assert 'onFailure{message=it.message?:"Не вдалося вибрати запис історії"}' in text
    assert '"/api/media/history/delete"' not in text
    assert '"/api/media/history/open"' not in text
    assert '"/history/delete"' not in text
    assert '"/history/open"' not in text
    assert '"history_id"' not in text
    assert 'endpointPath(endpointId,"/video/history")' not in text
    assert 'endpointPath(endpoint.endpointId,"/video/history")' not in text
    assert "LaunchedEffect(api.server,endpointId){reload()}" in text


def test_home_nonyoutube_regressions_restore_history_telegram_and_catalog_actions() -> None:
    text = _source()
    assert 'var telegramSourcesExpanded by rememberSaveable{mutableStateOf(false)}' in text
    assert 'Mdi(if(telegramSourcesExpanded)"chevron-up" else "chevron-down",16.dp,Muted)' in text
    assert 'val amazonUrl=external.optString("amazon_url").trim()' in text
    assert 'external.optString("amazon_label","Prime / BritBox")' in text
    assert 'val appleTvUrl=external.optString("apple_tv_url").trim()' in text
    assert 'external.optString("apple_tv_label","Apple TV")' in text
    assert 'openCatalogUrl(context,amazonUrl)' in text
    assert 'openCatalogUrl(context,appleTvUrl)' in text
    assert "defaultRecoveryActions" not in text
