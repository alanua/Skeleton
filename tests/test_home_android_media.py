from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_ACTIVITY = ROOT / "android/home/app/src/main/java/com/skeleton/home/MainActivity.kt"
BUILD_GRADLE = ROOT / "android/home/app/build.gradle.kts"


def _source() -> str:
    return MAIN_ACTIVITY.read_text(encoding="utf-8")


def test_home_release_metadata_is_1_4_34_150() -> None:
    text = BUILD_GRADLE.read_text(encoding="utf-8")

    assert "versionCode = 150" in text
    assert 'versionName = "1.4.34"' in text


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
    assert 'val remoteState=liveRemote.optInt("state",-99)' in text
    assert 'val remoteDuration=liveRemote.optDouble("duration",0.0).toFloat()' in text
    assert 'val videoId=liveRemote.optString("video_id","").ifBlank{lastGoodPlayer.optString("video_id","")}' in text
    assert 'val serverPos=(if(liveRemote.has("time"))liveRemote.optDouble("time",fallbackPos) else fallbackPos).toFloat()' in text
    assert 'val rawDuration=if(remoteDuration>1f)remoteDuration else lastGoodPlayer.optDouble("duration",0.0).toFloat()' in text
    assert 'val paused=when(remoteState){1->false;2,3->true;else->lastGoodPlayer.optBoolean("pause",false)}' in text
    assert 'remoteState in setOf(1,2,3)' in text
    assert 'runCatching{api.get(endpointPath(endpoint.endpointId,"/remote/status"))}' in text
    assert 'runCatching{api.get(endpointPath(endpointId,"/remote/status"))}' not in text
    assert "delay(if(endpoint.adapterKind!=\"samsung\"&&mode==\"kiosk\")850 else 2500)" in text
    assert "delay(4000)" not in text
    assert 'runCatching{api.get(endpointPath(endpoint.endpointId,"/remote/status"))}.onSuccess{youtubeRemoteStatus=it}\n                delay(850)' in text
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


def test_home_nonyoutube_regressions_restore_history_and_telegram_only() -> None:
    text = _source()
    assert 'var telegramSourcesExpanded by rememberSaveable{mutableStateOf(false)}' in text
    assert 'Mdi(if(telegramSourcesExpanded)"chevron-up" else "chevron-down",16.dp,Muted)' in text
    assert 'val preferred=historyPreferredSelection(endpoint.endpointId,item)' in text
    assert 'runCatching{api.put(endpointPath(endpoint.endpointId,"/video/selection"),preferred)}' in text


def test_history_selection_payload_excludes_resume_position():
    src = MAIN_ACTIVITY.read_text(encoding="utf-8")
    block = src[src.index("private fun historyPreferredSelection"):src.index("private fun mediaUiMode")]
    assert "position_seconds" not in block


def test_youtube_fast_status_drives_progress_and_poster():
    src = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'delay(850)' in src
    assert 'liveRemote.optDouble("time",fallbackPos)' in src
    assert 'val remoteDuration=liveRemote.optDouble("duration",0.0).toFloat()' in src
    assert '"https://i.ytimg.com/vi/$videoId/hqdefault.jpg"' in src
