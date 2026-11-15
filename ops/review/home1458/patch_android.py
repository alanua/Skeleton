from pathlib import Path


def patch_android(root: Path) -> None:
    path = root / 'app/src/main/java/com/skeleton/home/MainActivity.kt'
    text = path.read_text()

    def replace(old: str, new: str, count: int = 1) -> None:
        nonlocal text
        assert text.count(old) == count, ('source_boundary', old[:100], text.count(old))
        text = text.replace(old, new)

    if 'import androidx.compose.foundation.horizontalScroll' not in text:
        replace('import androidx.compose.foundation.verticalScroll', 'import androidx.compose.foundation.horizontalScroll\nimport androidx.compose.foundation.verticalScroll')

    # Use one projection for displayed normal-history rows AND their denominator.
    old = 'var query by remember{mutableStateOf("")};val canonical=remember(items){visibleHistoryItems(items)};val q=query.trim().lowercase(Locale.ROOT);val visible=if(q.isBlank())canonical else canonical.filter{x->listOf(x.optString("title"),x.optString("year"),x.optString("content_type"),x.optString("overview")).joinToString(" ").lowercase(Locale.ROOT).contains(q)}'
    new = 'var query by remember{mutableStateOf("")};val result=remember(items,query){homeHistoryPickerResult(items,query)};val q=query.trim().lowercase(Locale.ROOT);val visible=result.items'
    replace(old, new)
    start = text.index('@Composable private fun HistoryPickerDialog(')
    end = text.index('@Composable private fun HistoryPickerRow(', start)
    picker = text[start:end]
    old_counter = 'Text(if(q.isBlank())"${items.size}" else "${visible.size} з ${items.size}",'
    assert picker.count(old_counter) == 1
    picker = picker.replace(old_counter, 'Text(if(q.isBlank())"${result.total}" else "${visible.size} з ${result.total}",')
    text = text[:start] + picker + text[end:]

    # Bottom-of-card source icons are siblings of card-selection controls.
    start = text.index('@Composable private fun HistoryPickerRow(')
    end = text.index('\nprivate const val TRAKT_PACKAGE', start)
    row = text[start:end]
    assert row.rstrip().endswith('}}}}')
    row = row.rstrip()[:-1] + '\nSourceSiteIcons(sourceSiteLinks(item))\n}\n'
    text = text[:start] + row + text[end:]
    start = text.index('@Composable private fun HistoryEditorRow(')
    end = text.index('\nprivate data class EditableChannel', start)
    row = text[start:end]
    old_end = '    }\n    }\n}\n'
    assert row.endswith(old_end)
    row = row[:-len(old_end)] + '    }\n    SourceSiteIcons(sourceSiteLinks(item))\n    }\n}\n'
    text = text[:start] + row + text[end:]
    replace('return pages.mapNotNull(::sourceSiteLink).distinctBy{it.pageUrl}.take(5)', 'return pages.mapNotNull(::sourceSiteLink).distinctBy{it.host}.take(5)')
    icons = '''@Composable private fun SourceSiteIcons(links:List<SourceSiteLink>){
    if(links.isEmpty())return
    val context=LocalContext.current
    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(6.dp)){
        links.forEach{link->
            val bitmap=remoteBitmap(link.faviconUrls.first(),48,48)
            Box(Modifier.size(48.dp).clip(RoundedCornerShape(9.dp))
                .clickable(onClickLabel="Відкрити сторінку джерела "+link.host){openCatalogUrl(context,link.pageUrl)}
                .semantics{contentDescription="Джерело відео: "+link.host},contentAlignment=Alignment.Center){
                if(bitmap!=null)Image(bitmap,"Фавіконка "+link.host,Modifier.size(24.dp),contentScale=ContentScale.Fit)
                else Text(link.host.take(1).uppercase(Locale.ROOT),fontSize=14.sp,color=Muted)
            }
        }
    }
}

'''
    replace('@Composable private fun SourceSiteLinkRow(', icons + '@Composable private fun SourceSiteLinkRow(')

    # A bounded frame scrolls only when all usable controls cannot fit the window.
    frame = '''@Composable private fun HomeRemoteFrame(minimumHeight:Dp,content:@Composable ()->Unit){
    BoxWithConstraints(Modifier.fillMaxSize()){
        val contentHeight=maxOf(maxHeight,minimumHeight)
        Box(Modifier.fillMaxSize().verticalScroll(rememberScrollState())){
            Box(Modifier.fillMaxWidth().height(contentHeight)){content()}
        }
    }
}

'''
    replace('@Composable private fun VideoRemoteCard(', frame + '@Composable private fun VideoRemoteCard(')
    start = text.index('@Composable private fun VideoRemoteCard(')
    end = text.index('\n@Composable private fun TvRemoteCard(', start)
    block = text[start:end]
    marker = '    Column(Modifier.fillMaxSize().clip(RoundedCornerShape(16.dp)).background(Card)){'
    assert block.count(marker) == 1
    block = block.replace(marker, '    val sourceLinks=sourceSiteLinks(p)\n    HomeRemoteFrame((homeVideoMinimumHeight(LocalDensity.current.fontScale)+(if(sourceLinks.isEmpty())0f else 48f)).dp){\n' + marker)
    assert block.rstrip().endswith('    }\n}')
    block = block.rstrip()[:-1] + '    }\n}\n'
    block = block.replace('            TransportRow(p.optBoolean("pause"),false,onControl,onMute)', '            SourceSiteIcons(sourceLinks)\n            TransportRow(p.optBoolean("pause"),false,onControl,onMute)')
    text = text[:start] + block + text[end:]

    start = text.index('@Composable private fun YoutubeRemoteCard(')
    end = text.index('\n// Poster frame follows actual portrait art;', start)
    block = text[start:end]
    render = block.index('    Column(Modifier.fillMaxSize().clip(RoundedCornerShape(16.dp)).background(Card)){')
    new_render = '''    val sourceLinks=sourceSiteLinks(p,remotePlayer)
    BoxWithConstraints(Modifier.fillMaxSize().clip(RoundedCornerShape(16.dp)).background(Card)){
        val showSeek=running&&canSeek&&rawDuration>1f&&videoId.isNotBlank()
        val plan=homeYoutubeLayout(maxWidth.value,maxHeight.value,LocalDensity.current.fontScale,showSeek,running&&isLive,if(sourceLinks.isEmpty())0f else 48f)
        Box(Modifier.fillMaxSize().horizontalScroll(rememberScrollState()).verticalScroll(rememberScrollState())){
            Column(Modifier.width(plan.contentWidth.dp).height(plan.contentHeight.dp)){
                Box(Modifier.fillMaxWidth().height(plan.posterHeight.dp).background(Color(0xFF080A0C)),contentAlignment=Alignment.Center){
                    if(bitmap!=null)Image(bitmap,null,Modifier.size(plan.posterWidth.dp,plan.posterHeight.dp),contentScale=ContentScale.Fit)
                    else Mdi("youtube",((plan.dpadDiameter*.38f-14f)*1.2f).coerceAtLeast(24f).dp,Color(0xFFFF0000))
                }
                Column(Modifier.fillMaxWidth().padding(horizontal=12.dp).padding(bottom=12.dp)){
                    Box(Modifier.fillMaxWidth().height(plan.titleHeight.dp)){PlayerTitleLine(title,7.dp)}
                    Box(Modifier.fillMaxWidth().height(plan.seasonHeight.dp),contentAlignment=Alignment.TopCenter){
                        if(se.isNotBlank())Text(se,fontSize=11.sp,fontWeight=FontWeight.Bold,color=Accent,textAlign=TextAlign.Center)
                    }
                    if(showSeek){
                        Row(Modifier.fillMaxWidth().height(plan.seekInfoHeight.dp),verticalAlignment=Alignment.CenterVertically){
                            Text(fmt(seek.toDouble()),fontSize=11.sp,color=Muted)
                            Spacer(Modifier.weight(1f))
                            Text(if(isLive)"● НАЖИВО" else fmt(duration.toDouble()),fontSize=11.sp,color=if(isLive)Accent else Muted)
                        }
                        SkeletonRange(seek.coerceIn(0f,duration),{seek=it.coerceIn(0f,duration)},{onSeek(seek.coerceIn(0f,duration).toInt())},0f,duration,Modifier.fillMaxWidth())
                    }else if(running&&isLive){
                        Box(Modifier.fillMaxWidth().height(34.dp),contentAlignment=Alignment.CenterEnd){Text("● НАЖИВО",fontSize=11.sp,fontWeight=FontWeight.Bold,color=Accent)}
                    }
                    SourceSiteIcons(sourceLinks)
                    YoutubePanel(onRemote,onMute,volume==0,youtubeControls,plan.dpadDiameter.dp,plan.stackedSatellites,Modifier.fillMaxWidth().height(plan.panelHeight.dp))
                    VolumeRow(volume,onVolume,onMute)
                }
            }
        }
    }
}
'''
    text = text[:start] + block[:render] + new_render + text[end:]

    start = text.index('@Composable private fun YoutubePanel(')
    end = text.index('\n@Composable private fun CircleRemoteButton(', start)
    panel = '''@Composable private fun YoutubePanel(onRemote:(String)->Unit,onMute:()->Unit,muted:Boolean,controls:JSONObject,diameter:Dp,stacked:Boolean,modifier:Modifier=Modifier){
    val likeAvailable=controls.optBoolean("like_available",false)
    val liked=controls.optBoolean("liked",false)
    val ccAvailable=controls.optBoolean("subtitles_available",false)
    val ccEnabled=controls.optBoolean("subtitles_enabled",false)
    val side=48.dp
    if(stacked){
        Column(modifier,verticalArrangement=Arrangement.SpaceBetween,horizontalAlignment=Alignment.CenterHorizontally){
            Dpad(diameter,Modifier.size(diameter),onRemote)
            Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceEvenly){
                CircleLikeButton(side,liked,likeAvailable){onRemote("like")}
                CircleRemoteActionButton("subtitles",side,ccEnabled,ccAvailable){onRemote("subtitles")}
            }
            Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceEvenly){
                CircleRemoteButton("keyboard-return",side){onRemote("back")}
                CircleRemoteButton("volume-off",side,muted,onClick=onMute)
            }
        }
    }else{
        Box(modifier){
            Dpad(diameter,Modifier.size(diameter).align(Alignment.Center),onRemote)
            CircleLikeButton(side,liked,likeAvailable,Modifier.align(Alignment.TopStart).offset(y=8.dp)){onRemote("like")}
            CircleRemoteActionButton("subtitles",side,ccEnabled,ccAvailable,Modifier.align(Alignment.TopEnd).offset(y=8.dp)){onRemote("subtitles")}
            CircleRemoteButton("keyboard-return",side,modifier=Modifier.align(Alignment.BottomStart).offset(y=(-8).dp)){onRemote("back")}
            CircleRemoteButton("volume-off",side,muted,Modifier.align(Alignment.BottomEnd).offset(y=(-8).dp),onMute)
        }
    }
}
'''
    text = text[:start] + panel + text[end:]
    # Keep the numeric label responsive and increase actual volume/game touch targets.
    replace('Modifier.size(40.dp).offset(y=12.dp).clickable{onVolume(', 'Modifier.size(48.dp).offset(y=6.dp).clickable{onVolume(', 2)
    replace('}.width(36.dp))', '}.widthIn(min=36.dp))')
    start = text.index('@Composable private fun GameRemoteCard(')
    end = text.index('@Composable private fun GameRemoteKey(', start)
    game = text[start:end].replace('Modifier.size(44.dp)', 'Modifier.size(48.dp)').replace('Modifier.height(44.dp)', 'Modifier.height(48.dp)')
    text = text[:start] + game + text[end:]

    # Order all software-remote requests in the context that received the tap.
    start = text.index('@Composable private fun HomeScreen(')
    end = text.index('\n@OptIn(ExperimentalFoundationApi::class)', start)
    home = text[start:end]
    marker = '    Box(Modifier.fillMaxSize()){\n        AmbientPosterBackground'
    assert home.count(marker) == 1
    queue = '''    var controlError by remember(endpoint.endpointId){mutableStateOf<String?>(null)}
    val controlServer=api.server
    val controlReceiver=remember(controlServer){HomeApi().also{it.server=controlServer}}
    val controlQueue=remember(controlServer,endpoint.endpointId,mode,active){HomeControlQueue()}
    DisposableEffect(controlQueue){onDispose{controlQueue.close()}}
    LaunchedEffect(controlQueue){
        if(!active||controlServer==null)return@LaunchedEffect
        controlQueue.consume({cmd->
            val response=when(cmd.kind){
                "remote"->controlReceiver.post(endpointPath(endpoint.endpointId,"/remote/control"),endpointMutation(endpoint.endpointId,"action" to cmd.action,"phase" to "tap","expected_mode" to mode))
                "control"->controlReceiver.post(endpointPath(endpoint.endpointId,"/control/"+cmd.action),endpointMutation(endpoint.endpointId,"expected_mode" to mode))
                "seek"->controlReceiver.post(endpointPath(endpoint.endpointId,"/seek"),endpointMutation(endpoint.endpointId,"position" to cmd.value,"expected_mode" to mode))
                "volume"->controlReceiver.post(endpointPath(endpoint.endpointId,"/volume"),endpointMutation(endpoint.endpointId,"level" to cmd.value))
                else->throw IllegalArgumentException("Unknown control kind")
            }
            currentCoroutineContext().ensureActive()
            val resultError=response.optJSONObject("result")?.optString("error","").orEmpty()
            if(response.has("applied")&&!response.optBoolean("applied")||resultError.isNotBlank()&&resultError!="success")throw IllegalStateException("Control not confirmed")
            controlError=null
            if(cmd.kind!="remote")refresh()
        },{controlError="Команду не підтверджено. Перевірте з’єднання й повторіть."})
    }
    fun submitControl(command:HomeControlCommand){
        if(!controlQueue.submit(command))controlError="Пульт зайнятий. Повторіть натискання."
    }
'''
    home = home.replace(marker, queue + marker)
    pairs = {
        'onVolume={v->if(volumeReady){setEndpointVolume(v);scope.launch{runCatching{api.post(endpointPath(endpoint.endpointId,"/volume"),endpointMutation(endpoint.endpointId,"level" to v.coerceIn(0,100)))}}}},': 'onVolume={v->if(volumeReady){setEndpointVolume(v);submitControl(HomeControlCommand("volume",value=v.coerceIn(0,100)))}},',
        'onRemote={act->scope.launch{runCatching{api.post(endpointPath(endpoint.endpointId,"/remote/control"),endpointMutation(endpoint.endpointId,"action" to act,"phase" to "tap"))}}},': 'onRemote={act->submitControl(HomeControlCommand("remote",act))},',
        'onControl={ctl->scope.launch{runCatching{api.post(endpointPath(endpoint.endpointId,"/control/$ctl"),endpointMutation(endpoint.endpointId))};refresh()}},': 'onControl={ctl->submitControl(HomeControlCommand("control",ctl))},',
        'onSeek={pos->scope.launch{runCatching{api.post(endpointPath(endpoint.endpointId,"/seek"),endpointMutation(endpoint.endpointId,"position" to pos))};refresh()}},': 'onSeek={pos->submitControl(HomeControlCommand("seek",value=pos))},',
        'onMute={if(volumeReady)scope.launch{val v=if(volume==0)lastNonzero.coerceAtLeast(1) else 0;setEndpointVolume(v);runCatching{api.post(endpointPath(endpoint.endpointId,"/volume"),endpointMutation(endpoint.endpointId,"level" to v))};refresh()}},': 'onMute={if(volumeReady){val v=if(volume==0)lastNonzero.coerceAtLeast(1) else 0;setEndpointVolume(v);submitControl(HomeControlCommand("volume",value=v))}},',
    }
    for old,new in pairs.items():
        assert home.count(old) == 1, ('control_callback', old[:60])
        home = home.replace(old,new)
    home = home.replace('            Spacer(Modifier.height(6.dp))\n            PersistentHomeRemotes(', '            controlError?.let{Text(it,color=Accent,fontSize=12.sp,maxLines=2,modifier=Modifier.fillMaxWidth().padding(bottom=4.dp))}\n            Spacer(Modifier.height(6.dp))\n            PersistentHomeRemotes(')
    text = text[:start] + home + text[end:]
    path.write_text(text)


if __name__ == '__main__':
    import sys
    patch_android(Path(sys.argv[1]))
