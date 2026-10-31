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
    assert 'val remoteState=liveRemote.optInt("state",-99)' in text
    assert 'val remoteDuration=liveRemote.optDouble("duration",0.0).toFloat()' in text
    assert 'val remoteVideoId=liveRemote.optString("video_id","")' in text
    assert 'val videoId=remoteVideoId.ifBlank{canonicalVideoId}' in text
    assert 'val serverPos=(if(liveRemote.has("time"))liveRemote.optDouble("time",fallbackPos) else fallbackPos).toFloat()' in text
    assert 'val rawDuration=if(remoteDuration>1f)remoteDuration else lastGoodPlayer.optDouble("duration",0.0).toFloat()' in text
    assert 'val paused=when(remoteState){1->false;2,3->true;else->lastGoodPlayer.optBoolean("pause",false)}' in text
    assert 'remoteState in setOf(1,2,3)' in text
    assert 'runCatching{api.get(endpointPath(endpoint.endpointId,"/remote/status"))}' in text
    assert 'runCatching{api.get(endpointPath(endpointId,"/remote/status"))}' not in text
    assert "delay(if(endpoint.adapterKind!=\"samsung\"&&mode==\"kiosk\")850 else 2500)" in text
    assert "delay(4000)" not in text
    assert 'runCatching{api.get(endpointPath(endpoint.endpointId,"/remote/status"))}.onSuccess{status->' in text
    assert 'delay(if(System.currentTimeMillis()<fastUntil)250 else 850)' in text
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
    assert 'val remoteDuration=liveRemote.optDouble("duration",0.0).toFloat()' in src
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


def _source_site_helpers() -> str:
    return _source().split("internal data class SourceSiteLink", 1)[1].split(
        "@Composable private fun SourceSiteLinkRow", 1
    )[0]


def test_source_sites_render_below_all_three_work_cards() -> None:
    text = _source()
    editor = text.split("@Composable private fun HistoryEditorRow", 1)[1].split(
        "private data class EditableChannel", 1
    )[0]
    picker = text.split("@Composable private fun HistoryPickerRow", 1)[1].split(
        "private const val TRAKT_PACKAGE", 1
    )[0]
    for card in (editor, picker):
        assert "SourceSiteLinkRow(sourceSiteLinks(item))" in card
        assert card.index("SourceSiteLinkRow") > card.index('item.optString("overview")')
    # The selection Row ends before the favicon row: browser clicks cannot select a work.
    assert "}};SourceSiteLinkRow(sourceSiteLinks(item))}}" in picker
    assert ";Column{Row(" in picker
    assert "SourceSiteLinkRow(sourceSiteLinks(selectedOrTrailer,job))" in text
    active = text.split("val selectedOrTrailer=", 1)[1]
    assert active.index("SourceSiteLinkRow") > active.index('VideoToggleControl("autoplay-repeat"')


def test_source_sites_accept_only_explicit_provenance_and_keep_future_api_fields() -> None:
    helper = _source_site_helpers()
    collector = helper.split("internal fun sourceSiteLinks", 1)[1]
    assert 'record.optString("source_page_url","")' in collector
    assert 'listOf("source_sites","source_pages")' in collector
    assert 'site.optString("source_page_url","")' in collector
    assert 'site.optString("page_url","")' in collector
    assert 'record.optString("page_url"' not in collector
    assert "records.filterNotNull()" in collector
    assert 'optJSONArray(field)?.objects().orEmpty()' in collector
    assert "pages.mapNotNull(::sourceSiteLink).distinctBy{it.pageUrl}.take(5)" in collector
    for forbidden in ('optString("url"', 'optJSONObject("stream")', 'optString("poster"', 'optString("favicon_url"'):
        assert forbidden not in helper
    # History dedupe retains the original API objects, including new provenance fields.
    history = _source().split("internal fun canonicalHistoryItems", 1)[1].split("\n}", 1)[0]
    assert "return items.filter" in history
    assert "JSONObject(" not in history
    assert 'job=j;sources=j.optJSONArray("sources")?.objects().orEmpty()' in _source()


def test_source_sites_read_explicit_discovery_and_history_pages_before_arrays() -> None:
    collector = _source_site_helpers().split("internal fun sourceSiteLinks", 1)[1]
    source_page = collector.index('pages.add(record.optString("source_page_url",""))')
    discovery_page = collector.index('pages.add(record.optString("discovery_page_url",""))')
    history_page = collector.index('pages.add(record.optString("history_page_url",""))')
    arrays = collector.index('listOf("source_sites","source_pages")')
    assert source_page < discovery_page < history_page < arrays
    assert "pages.mapNotNull(::sourceSiteLink).distinctBy{it.pageUrl}.take(5)" in collector


def test_source_site_url_validation_is_pure_and_conservative() -> None:
    helper = _source_site_helpers()
    validator = helper.split("internal fun sourceSiteLink(raw:String)", 1)[1].split(
        "internal fun sourceSiteLinks", 1
    )[0]
    assert "java.net.URI(raw.trim())" in validator
    assert 'scheme !in setOf("http","https")' in validator
    assert "uri.rawUserInfo!=null" in validator
    assert "!host.contains('.')" in validator
    assert "!host.last().isLetter()" in validator  # rejects numeric IPv4 and IPv6 literals
    assert '"localhost","local","internal","lan"' in validator
    assert "media.containsMatchIn(path)" in validator
    assert "java.net.URLDecoder.decode(part" in validator
    assert "media.containsMatchIn(decoded)" in validator
    assert "token|secret|password" in validator
    assert "signature|credential" in validator
    assert "uri.rawPath.orEmpty()" in validator
    assert 'else "?$query"' in validator
    for extension in ("m3u8", "mpd", "mp4", "mkv", "webm", "mp3"):
        assert extension in validator
    for forbidden in ("android.net", "Uri.", "openConnection", "InetAddress", "URL(raw"):
        assert forbidden not in validator


def test_source_site_favicon_privacy_fallback_and_browser_accessibility() -> None:
    text = _source()
    assert 'val faviconUrl:String get()="https://$host/favicon.ico"' in text
    row = text.split("@Composable private fun SourceSiteLinkRow", 1)[1].split(
        "private fun openCatalogUrl", 1
    )[0]
    assert "connection.instanceFollowRedirects=false" in row
    assert "readBytesLimited(128*1024)" in row
    assert 'Mdi("web",18.dp,Muted)' in row
    assert "contentDescription=" in row
    assert "onClickLabel=" in row
    assert "openCatalogUrl(context,link.pageUrl)" in row
    assert "Intent(Intent.ACTION_VIEW,Uri.parse(url))" in text
    assert "link.faviconUrl" in row
    assert "stream" not in row


def _source_site_detail_dialog() -> str:
    return _source().split("@Composable private fun VideoDetailDialog", 1)[1].split(
        "@Composable private fun SelectorField", 1
    )[0]


def test_source_site_detail_dialog_receives_selected_source_links() -> None:
    text = _source()
    assert "VideoDetailDialog(context,cat,bm,external,sourceLinks=detailSourceLinks,onDismiss={detailOpen=false})" in text
    assert "sourceLinks:List<SourceSiteLink>" in _source_site_detail_dialog()
    # Selection already resolves episode/quality/voice and trailer before collecting links.
    selection = text.split("val selectedCandidate=", 1)[1].split("val selectedIsTrailer=", 1)[0]
    assert 'it.optString("season")==season&&it.optString("episode")==episode' in selection
    assert 'it.optString("quality")==quality&&sourceVoice(it)==voice' in selection
    assert "val selectedOrTrailer=selectedCandidate ?: sources.firstOrNull" in selection
    assert "val detailSourceLinks=if(selectedOrTrailer!=null)sourceSiteLinks(selectedOrTrailer) else sourceSiteLinks(job)" in selection
    # Known sources with missing provenance stay empty; unrelated job sites are not merged.
    assert "sourceSiteLinks(selectedOrTrailer,job)" not in selection
    assert "ifEmpty" not in selection
    assert "!!" not in selection
    assert "sourceSiteLinks(" not in _source_site_detail_dialog()
    assert text.count("internal fun sourceSiteLinks(") == 1
    assert text.count("@Composable private fun SourceSiteLinkRow(") == 1


def test_source_site_detail_dialog_bottom_row_preserves_metadata_and_scrolling() -> None:
    dialog = _source_site_detail_dialog()
    assert "Column(Modifier.weight(1f).verticalScroll(rememberScrollState())" in dialog
    assert "contentScale=ContentScale.Crop" in dialog
    assert "Dialog(onDismissRequest=onDismiss" in dialog
    assert "clickable(onClick=onDismiss)" in dialog
    for rating in ("tmdb", "imdb", "mdblist", "trakt", "moviebase"):
        assert f"CatalogMetric(R.drawable.{rating}_brand" in dialog
    assert 'val overview=cat.optString("overview")' in dialog
    assert "Text(overview,fontSize=16.sp,lineHeight=24.sp" in dialog
    for field in ("genres", "original_language", "certification", "release_date", "original_title", "countries", "networks", "companies"):
        assert f'"{field}"' in dialog
    assert dialog.index("SourceSiteLinkRow(sourceLinks)") > dialog.index('DetailLine("Компанії"')
    assert dialog.split("SourceSiteLinkRow(sourceLinks)", 1)[1].strip().startswith("Spacer(Modifier.height(28.dp))")
    for forbidden in ("api.get", "api.post", "LaunchedEffect", "sourceLinks!!", "sourceLinks?"):
        assert forbidden not in dialog


def test_source_site_detail_browser_taps_do_not_dismiss_dialog() -> None:
    text = _source()
    row = text.split("@Composable private fun SourceSiteLinkRow", 1)[1].split(
        "private fun java.io.InputStream.readBytesLimited", 1
    )[0]
    assert 'clickable(onClickLabel="Відкрити ${link.host}"){openCatalogUrl(context,link.pageUrl)}' in row
    assert "if(links.isEmpty())return" in row
    assert "onDismiss" not in row
    assert "detailOpen" not in row
    browser = text.split("private fun openCatalogUrl", 1)[1].split(
        "@Composable private fun CatalogMetric", 1
    )[0]
    assert "runCatching{context.startActivity(Intent(Intent.ACTION_VIEW,Uri.parse(url))" in browser
    assert "onDismiss" not in browser
    assert "SourceSiteLinkRow(sourceLinks)" in _source_site_detail_dialog()
