from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_ACTIVITY = ROOT / "android/home/app/src/main/java/com/skeleton/home/MainActivity.kt"
BUILD_GRADLE = ROOT / "android/home/app/build.gradle.kts"


def _source() -> str:
    return MAIN_ACTIVITY.read_text(encoding="utf-8")


def test_home_release_metadata_is_1_4_39_155() -> None:
    text = BUILD_GRADLE.read_text(encoding="utf-8")

    assert "versionCode = 155" in text
    assert 'versionName = "1.4.39"' in text


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
    assert 'val remotePlayer=remote.optJSONObject("player") ?: JSONObject()' in text
    assert 'val remoteTitle=remotePlayer.optString("display-title",remotePlayer.optString("title",remotePlayer.optString("media-title",""))).trim()' in text
    assert 'val canonicalTitle=lastGoodPlayer.optString("display-title",lastGoodPlayer.optString("title",lastGoodPlayer.optString("media-title",""))).trim()' in text
    assert 'val remotePoster=remotePlayer.optString("poster_landscape",remotePlayer.optString("poster",""))' in text
    assert 'val canonicalPoster=lastGoodPlayer.optString("poster_landscape",lastGoodPlayer.optString("poster",""))' in text
    assert 'val remoteState=if(sameVideo)liveRemote.optInt("state",-99) else -99' in text
    assert 'val remoteDuration=if(sameVideo)liveRemote.optDouble("duration",0.0).toFloat() else 0f' in text
    assert 'val remoteVideoId=liveRemote.optString("video_id","")' in text
    assert 'val videoId=if(!sameVideo&&canonicalVideoId.isNotBlank())canonicalVideoId else remoteVideoId.ifBlank{canonicalVideoId}' in text
    assert 'val serverPos=(if(sameVideo&&liveRemote.has("time"))liveRemote.optDouble("time",fallbackPos) else fallbackPos).toFloat()' in text
    assert 'val rawDuration=if(remoteDuration>1f)remoteDuration else lastGoodPlayer.optDouble("duration",0.0).toFloat()' in text
    assert 'val paused=when(remoteState){1->false;2,3->true;else->lastGoodPlayer.optBoolean("pause",false)}' in text
    assert 'remoteState in setOf(1,2,3)' in text
    assert 'runCatching{api.get(endpointPath(endpoint.endpointId,"/remote/status"))}' in text
    assert 'runCatching{api.get(endpointPath(endpointId,"/remote/status"))}' not in text
    assert "delay(if(endpoint.adapterKind!=\"samsung\"&&mode==\"kiosk\")850 else 2500)" in text
    assert "delay(4000)" not in text
    assert 'runCatching{api.get(endpointPath(endpoint.endpointId,"/remote/status"))}.onSuccess{status->' in text
    assert 'delay(if(System.currentTimeMillis()<fastUntil)250 else 850)' in text
    assert "val remoteControls=remote.optJSONObject" in text
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
    assert 'internal fun savedVideoHistoryPath():String="/api/video/history"' in text
    assert 'internal fun historyKey(item:JSONObject):String=item.cleanText("history_key",item.cleanText("content_key",item.cleanText("history_id")))' in text
    assert 'private fun mediaHistoryDeletePath(endpointId:String,item:JSONObject):String="/api/media/history/${Uri.encode(historyKey(item))}?endpoint=${Uri.encode(endpointId)}"' in text
    assert "canonicalHistoryItems(api.get(mediaHistoryPath(endpointId)).optJSONArray" in text
    assert "setEndpointHistory(historyItemsAfterRead(history,saved?.getOrNull(),watched.getOrNull(),true,savedHistory))" in text
    assert "watchedHistoryForSavedLibrary(savedBase,it)" in text
    assert "saved?.getOrNull()?.let{setEndpointSavedHistory(canonicalHistoryItems(it))}" in text
    assert 'val ok=if(endpoint.adapterKind=="home_edge_tv")saved?.isSuccess==true else watched.isSuccess' in text
    assert 'saved?.isSuccess==true||watched.isSuccess' not in text
    assert "val historyByEndpoint=remember{mutableStateMapOf<String,List<JSONObject>>()}" in text
    assert "val savedHistoryByEndpoint=remember{mutableStateMapOf<String,List<JSONObject>>()}" in text
    assert "historyEpisodeLabel(item)" in text
    assert 'api.get(savedVideoHistoryPath())' in text
    assert 'api.get(mediaHistoryPath(endpointId))' in text
    assert 'api.get(mediaHistoryPath(endpoint.endpointId))' in text
    assert 'api.delete(mediaHistoryDeletePath(endpointId,item))' in text
    assert 'val preferred=historyPreferredSelection(endpoint.endpointId,item)' in text
    assert 'confirmEndpointSelection(preferred)' in text
    assert 'val confirmed=confirmedVideoSelectionFromPut(endpoint.endpointId,ack)' in text
    assert 'throw IllegalStateException("Вибір не підтверджено")' in text
    assert 'selection=JSONObject(body.toString())' not in text
    identity = text.split("private fun videoSelectionIdentity", 1)[1].split("internal fun videoSelectionIdentityForTest", 1)[0]
    assert "Uri.encode" not in identity
    assert 'VideoSelectionIdentityFields=listOf("job_id","source_id","season","episode","voice")+listOf("endpoint_id")' in text
    assert '"${value.length}:$value"' in identity
    assert 'internal fun confirmedVideoSelectionFromPut(endpointId:String,ack:JSONObject):JSONObject?' in text
    assert 'val direct=listOf(j.optJSONObject("selection"),j.optJSONObject("preferred"),j.optJSONObject("video_selection")).filterNotNull()' in text
    assert 'val nested=listOf("data","result","response","payload").mapNotNull{j.optJSONObject(it)}.flatMap(::candidates)' in text
    assert 'val canonicalSource=canonicalSourceForSelection(body,sources)' in text
    assert 'if(sourceId.isNotBlank())playable.firstOrNull{it.cleanText("source_id")==sourceId&&matchesIntent(it)}?.let{return it}' in text
    assert 'if(season.isBlank()&&episode.isBlank()&&voice.isBlank())return null' in text
    assert 'playable.firstOrNull{season.isBlank()||it.cleanText("season")==season}' not in text
    assert 'if(body.cleanText("source_id").isBlank())throw IllegalStateException("Збережений твір відкрито без підтвердженого джерела")' in text
    assert 'onFailure{message=it.message?:"Не вдалося вибрати запис історії"}' in text
    assert '"/api/video/history/saved"' not in text
    assert '"/api/video/history/library"' not in text
    assert '"/api/media/library/saved"' not in text
    assert '"/api/media/history/delete"' not in text
    assert '"/api/media/history/open"' not in text
    assert '"/history/delete"' not in text
    assert '"/history/open"' not in text
    assert 'endpointPath(endpointId,"/video/history")' not in text
    assert 'endpointPath(endpoint.endpointId,"/video/history")' not in text
    assert "LaunchedEffect(api.server,endpointId){reload()}" in text



def test_home_history_cards_prefer_server_canonical_work_identity() -> None:
    text = _source()
    # A source/discovery job may have a stale history_id. The stable work key
    # must drive both the card dedupe and the saved-library progress join.
    assert 'internal fun historyKey(item:JSONObject):String=item.cleanText("history_key",item.cleanText("content_key",item.cleanText("history_id")))' in text
    assert "return items.filter{val k=historyKey(it);k.isBlank()||seen.add(k)}" in text
    assert "val key=historyKey(item)" in text
    assert "val savedKeys=savedLibrary.mapNotNull{historyKey(it).takeIf(String::isNotBlank)}.toSet()" in text
    assert "return watchedHistory.filter{historyKey(it) in savedKeys}" in text
    assert 'private fun mediaHistoryDeletePath(endpointId:String,item:JSONObject):String="/api/media/history/${Uri.encode(historyKey(item))}?endpoint=${Uri.encode(endpointId)}"' in text
    assert 'historyKey(item:JSONObject):String=item.cleanText("history_id",' not in text

def test_home_media_volume_label_uses_measured_gap_above_slider() -> None:
    text = _source()
    block = text.split("@Composable private fun VolumeRow", 1)[1].split("@Composable private fun YoutubePanel", 1)[0]

    assert "Modifier.fillMaxWidth().height(54.dp)" in block
    assert "var labelWidthPx by remember{mutableIntStateOf(0)}" in block
    assert "var labelHeightPx by remember{mutableIntStateOf(0)}" in block
    assert "val sliderTopPx=with(density){20.dp.roundToPx()}" in block
    assert "val measuredGapPx=with(density){3.dp.roundToPx()}" in block
    assert "val labelY=(sliderTopPx-measuredGapPx-labelHeightPx).coerceAtLeast(0)" in block
    assert ".offset{IntOffset(labelX,labelY)}.width(36.dp)" in block
    assert "SkeletonRange(v,{v=it},{onVolume((v/5f).toInt()*5)},0f,100f,Modifier.fillMaxWidth().offset(y=20.dp))" in block


def test_home_nonyoutube_regressions_restore_history_and_telegram_only() -> None:
    text = _source()
    assert 'var telegramSourcesExpanded by rememberSaveable{mutableStateOf(false)}' in text
    assert 'Mdi(if(telegramSourcesExpanded)"chevron-up" else "chevron-down",16.dp,Muted)' in text
    assert 'val preferred=historyPreferredSelection(endpoint.endpointId,item)' in text
    assert 'confirmEndpointSelection(preferred)' in text


def test_history_selection_payload_excludes_resume_position():
    src = MAIN_ACTIVITY.read_text(encoding="utf-8")
    block = src[src.index("private fun historyPreferredSelection"):src.index("private fun mediaUiMode")]
    assert "position_seconds" not in block


def test_youtube_fast_status_drives_progress_and_poster():
    src = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert 'delay(if(System.currentTimeMillis()<fastUntil)250 else 850)' in src
    assert 'liveRemote.optDouble("time",fallbackPos)' in src
    assert 'val remoteDuration=if(sameVideo)liveRemote.optDouble("duration",0.0).toFloat() else 0f' in src
    assert '"https://i.ytimg.com/vi/$videoId/hqdefault.jpg"' in src


def test_home_header_layout_video_only_beyond_home() -> None:
    text = _source()
    assert 'Header(ui("Головна")' in text
    assert 'Header(ui("Відео")' in text
    assert 'captureCharged=captureCharged,captureEnabled=captureEnabled,onCapture={capturePress()},hyperion=hyperion,onHyperion={onHyperion(!hyperion)}' in text
    devices = text.split("@Composable private fun DevicesScreen", 1)[1].split("private data class SkItem", 1)[0]
    skeleton = text.split("@Composable private fun SkeletonScreen", 1)[1]
    assert 'Header(ui("Пристрої")' not in devices
    assert 'Header(ui("СК")' not in skeleton


def test_youtube_fast_start_window_is_subsecond() -> None:
    text = _source()
    assert "fastUntil=System.currentTimeMillis()+12000L" in text
    assert "delay(if(System.currentTimeMillis()<fastUntil)250 else 850)" in text
    assert 'val running=lastGoodPlayer.optBoolean("running") || (videoId.isNotBlank()&&remoteState in setOf(1,2,3))' in text
    assert 'val remotePlayer=remote.optJSONObject("player") ?: JSONObject()' in text
    assert 'videoId.isNotBlank()->"https://i.ytimg.com/vi/$videoId/hqdefault.jpg"' in text



def test_home_and_video_header_keep_connection_status() -> None:
    text = _source()
    home_line = next(line for line in text.splitlines() if 'Header(ui("Головна")' in line)
    video_line = next(line for line in text.splitlines() if 'Header(ui("Відео")' in line)
    assert "showStatus=false" not in home_line
    assert "showStatus=false" not in video_line
    assert 'Text(if(connected) "Підключено" else "Недоступно"' in text
    assert 'background(if(connected)Green else Muted)' in text


def test_native_home_episode_arrows_use_real_backend_route_and_double_size() -> None:
    text = _source()
    assert 'val target=api.get("/api/media/target").optString("target","")' in text
    assert 'val expected=when(endpointId){"home_edge_tv"->"tv";"samsung_tv"->"samsung";else->""}' in text
    assert 'if(expected.isBlank()||target!=expected)throw IllegalStateException(' in text
    assert 'api.post("/api/video/episode/step",JSONObject().put("direction",direction))' in text
    assert 'endpointPath(endpointId,"/video/episode/step")' not in text
    assert 'Modifier.align(Alignment.CenterStart).width(maxOf(frameSide,72.dp)).fillMaxHeight()' in text
    assert 'Modifier.align(Alignment.CenterEnd).width(maxOf(frameSide,72.dp)).fillMaxHeight()' in text
    assert 'Text("‹",fontSize=54.sp,lineHeight=56.sp' in text
    assert 'Text("›",fontSize=54.sp,lineHeight=56.sp' in text
    assert 'finally{episodeStepping=false}' in text


def test_native_youtube_remote_errors_are_not_silenced() -> None:
    text = _source()
    assert 'api.post(endpointPath(endpoint.endpointId,"/remote/control"),endpointMutation(endpoint.endpointId,"action" to act,"phase" to "tap"))' in text
    assert 'if(endpoint.adapterKind!="samsung"&&!reply.optBoolean("applied",false))' in text
    assert 'android.widget.Toast.makeText(context,"Пульт YouTube: "' in text
    assert 'onRemote={act->scope.launch{runCatching{api.post' not in text


def test_youtube_current_video_metadata_overrides_stale_cached_remote() -> None:
    text = _source()
    assert 'val videoId=if(!sameVideo&&canonicalVideoId.isNotBlank())canonicalVideoId else remoteVideoId.ifBlank{canonicalVideoId}' in text
    assert 'sameVideo&&remoteTitle.isNotBlank()->remoteTitle' in text
    assert 'canonicalTitle.isNotBlank()->canonicalTitle' in text
    assert 'val poster=if(sameVideo)remotePoster.ifBlank{canonicalPoster} else canonicalPoster' in text
    assert 'val serverPos=(if(sameVideo&&liveRemote.has("time"))' in text


def test_youtube_cached_ownership_scopes_like_subtitles_and_seekability() -> None:
    text = _source()
    card = text.split("@Composable private fun YoutubeRemoteCard", 1)[1].split("@Composable private fun VideoRemoteCard", 1)[0]
    assert 'val remoteControls=remote.optJSONObject("youtube_context")?.optJSONObject("controls")' in card
    assert 'val youtubeControls=if(sameVideo)remoteControls else JSONObject()' in card
    assert 'val canSeek=if(sameVideo)liveRemote.optBoolean("is_seekable",!isLive&&rawDuration>1.0) else !isLive&&rawDuration>1.0' in card
    assert 'val remoteDuration=if(sameVideo)liveRemote.optDouble("duration",0.0).toFloat() else 0f' in card


def test_home_discovery_prefers_last_verified_endpoint_to_avoid_tailnet_lag() -> None:
    text = _source()
    assert 'val lastHealthy=server' in text
    assert 'val candidates=if(lastHealthy!=null&&lastHealthy in servers)' in text
    assert 'listOf(lastHealthy)+servers.filterNot{it==lastHealthy} else servers' in text
    assert 'for (s in candidates) try {' in text
    assert 'if (ok) { server = s; return@withContext s }' in text
    assert 'server = null; null' in text
