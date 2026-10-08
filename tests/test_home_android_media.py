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


def test_home_tv_youtube_uses_fast_remote_bootstrap_without_losing_player_authority() -> None:
    text = _source()

    assert 'api.get(endpointPath(endpoint.endpointId,"/snapshot"))' not in text
    assert '"/snapshot"' not in text
    assert 'api.get(endpointPath(endpoint.endpointId,"/player"))' in text
    assert "parseMediaEndpointPlayerState" in text
    assert 'runCatching{api.get(endpointPath(endpoint.endpointId,"/remote/status"))}.onSuccess{youtubeRemoteStatus=it}' in text
    assert "delay(4000)" not in text
    assert "delay(850)" in text
    assert 'val remotePlayer=remote.optJSONObject("player") ?: JSONObject()' in text
    assert 'val remoteYoutube=remote.optJSONObject("youtube_player") ?: remote' in text
    assert "val remoteCoherent=" in text
    assert "val sameVideo=" in text
    assert "val useRemoteBootstrap=" in text
    assert 'remoteState in setOf(1,2,3)' in text
    assert 'val canonicalPoster=lastGoodPlayer.optString("poster_landscape",lastGoodPlayer.optString("poster",""))' in text
    assert 'val fastPoster=remotePlayer.optString("poster_landscape",remotePlayer.optString("poster",""))' in text
    assert "canonicalDuration>1f" in text
    assert "sameVideo&&remoteDuration>1f" in text
    assert "remoteState!=1" in text
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


def test_home_regression_repair_restores_only_confirmed_lost_behaviors() -> None:
    text = _source()

    assert 'var requestedMode by remember(endpoint.endpointId){mutableStateOf<String?>(null)}' in text
    assert 'var requestedModeAt by remember(endpoint.endpointId){mutableStateOf(0L)}' in text
    assert 'System.currentTimeMillis()-requestedModeAt>=5000L' in text
    assert 'if(!applied){requestedMode=null;requestedModeAt=0L}' in text
    assert 'var telegramSourcesExpanded by rememberSaveable{mutableStateOf(false)}' in text
    assert 'Mdi(if(telegramSourcesExpanded)"chevron-up" else "chevron-down",16.dp,Muted)' in text
    assert 'val amazonUrl=external.optString("amazon_url").trim()' in text
    assert 'external.optString("amazon_label","Prime / BritBox")' in text
    assert 'val appleTvUrl=external.optString("apple_tv_url").trim()' in text
    assert 'external.optString("apple_tv_label","Apple TV")' in text
    assert 'openCatalogUrl(context,amazonUrl)' in text
    assert 'openCatalogUrl(context,appleTvUrl)' in text
    assert "defaultRecoveryActions" not in text
    assert '"apple_tv"->"Apple TV"' not in text
    assert '"amazon_devices","amazon_fire_tv"->"Amazon"' not in text


def test_home_regression_repair_preserves_approved_newer_contracts() -> None:
    text = _source()

    assert 'val endpoint=endpointById(endpoints,endpointId)' in text
    assert 'endpointVolumes[endpoint.endpointId]=clamped' in text
    assert 'endpointVolumeReady[endpoint.endpointId]=true' in text
    assert 'api.post("/api/media/handoff",JSONObject().put("action","capture").put("source",endpoint.endpointId))' in text
    assert 'api.post("/api/media/handoff",JSONObject().put("target",endpoint.endpointId))' in text
    assert 'private fun mediaHistoryPath(endpointId:String):String="/api/media/history?endpoint=${Uri.encode(endpointId)}"' in text
    assert 'runCatching{endpointVideoSelection(api,endpoint.endpointId)}.onSuccess{latest->' in text
    assert 'if(latestIdentity!=selectionIdentity)applyEndpointSelection(latest,true)' in text
    assert 'fetchHomeUpdate' in text
    assert '"/api/native/app-update"' in text
    assert 'api.put("/api/media/target"' not in text
    assert '"/api/samsung/media/' not in text
