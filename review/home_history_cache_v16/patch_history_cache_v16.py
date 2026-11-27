"""Home history cache: exact 1.4.59 source patch, no deployment."""
import hashlib
BASE='15a1487a0d46b670ec09b649822f419b2db4c8d6944e9f0b3939ca587277d7c9'
def once(s,a,b):
    if s.count(a)!=1:raise ValueError('anchor '+a[:120])
    return s.replace(a,b,1)
def patch(s):
    if hashlib.sha256(s.encode()).hexdigest()!=BASE:raise ValueError('source mismatch')
    s=once(s,'private suspend fun savedVideoHistoryItemsPaged(api:HomeApi,onPreview:(HomeHistoryPreview?)->Unit):List<JSONObject>{','private suspend fun savedVideoHistoryItemsPaged(api:HomeApi,onPreview:(HomeHistoryPreview?)->Unit,onRevision:(String?)->Unit):List<JSONObject>{')
    s=once(s,'            return canonicalHistoryItems(rows)\n','            onRevision(null)\n            return canonicalHistoryItems(rows)\n')
    s=once(s,'            return canonical\n        }\n        offset=next','            onRevision(revision)\n            return canonical\n        }\n        offset=next')
    s=once(s,'private suspend fun savedVideoHistoryItemsWithRetry(api:HomeApi,attempts:Int=2,onPreview:(HomeHistoryPreview?)->Unit={}):List<JSONObject>{','private suspend fun savedVideoHistoryItemsWithRetry(api:HomeApi,attempts:Int=2,onPreview:(HomeHistoryPreview?)->Unit={},onRevision:(String?)->Unit={}):List<JSONObject>{')
    s=once(s,'savedVideoHistoryItemsPaged(api,onPreview)','savedVideoHistoryItemsPaged(api,onPreview,onRevision)')
    s=once(s,'@Composable private fun HistoryPickerDialog(api:HomeApi,items:List<JSONObject>,preview:HomeHistoryPreview?,loading:Boolean,error:String,complete:Boolean,onAtlas:()->Unit,onSelect:(JSONObject)->Unit,onDismiss:()->Unit){\n    var query by remember{mutableStateOf("")}\n    var filters by remember{mutableStateOf(HomeLibraryFilters())}', '@Composable private fun HistoryPickerDialog(api:HomeApi,items:List<JSONObject>,preview:HomeHistoryPreview?,loading:Boolean,error:String,complete:Boolean,pickerState:HomeHistoryPickerState,listState:androidx.compose.foundation.lazy.LazyListState,onAtlas:()->Unit,onSelect:(JSONObject)->Unit,onDismiss:()->Unit){\n    val query=pickerState.query\n    val filters=pickerState.filters')
    a=s.index('@Composable private fun HistoryPickerDialog'); b=s.index('@Composable private fun HistoryPickerRow',a)
    picker=once(s[a:b],'value=query,onValueChange={query=it},singleLine=true,textStyle=androidx.compose.ui.text.TextStyle(color=Text,fontSize=14.sp),modifier=Modifier.weight(1f).focusRequester(focusRequester),decorationBox={inner->Box(Modifier.fillMaxWidth()){if(query.isEmpty())Text(ui("Пошук в історії")','value=query,onValueChange={pickerState.query=it},singleLine=true,textStyle=androidx.compose.ui.text.TextStyle(color=Text,fontSize=14.sp),modifier=Modifier.weight(1f).focusRequester(focusRequester),decorationBox={inner->Box(Modifier.fillMaxWidth()){if(query.isEmpty())Text(ui("Пошук в історії")')
    s=s[:a]+picker+s[b:]
    s=once(s,'HomeLibraryFilterPanel(filters,expanded,canonical,{filters=it},{filters=HomeLibraryFilters();query=""},onAtlas)','HomeLibraryFilterPanel(filters,expanded,canonical,{pickerState.filters=it},{pickerState.filters=HomeLibraryFilters();pickerState.query=""},onAtlas)')
    s=once(s,'                if(loading)LinearProgressIndicator(Modifier.fillMaxWidth().height(2.dp),color=Accent)\n                if(error.isNotBlank())Text(error,fontSize=11.sp,color=Color(0xFFFF8A80))','                if(loading&&!complete)LinearProgressIndicator(Modifier.fillMaxWidth().height(2.dp),color=Accent)\n                if(error.isNotBlank())Text(error,fontSize=11.sp,color=Color(0xFFFF8A80))')
    s=once(s,'LazyColumn(Modifier.weight(1f).padding(horizontal=10.dp),contentPadding=PaddingValues(bottom=18.dp)){items(visible,key={historyKey(it).ifBlank{it.optString("title")}}){item->HistoryPickerRow','LazyColumn(Modifier.weight(1f).padding(horizontal=10.dp),state=listState,contentPadding=PaddingValues(bottom=18.dp)){items(visible,key={historyKey(it).ifBlank{it.optString("title")}}){item->HistoryPickerRow')
    assert s.count('SourceSiteIcons(sourceSiteLinks(item))')==2
    s=s.replace('    SourceSiteIcons(sourceSiteLinks(item))\n','',1)
    s=once(s,'SourceSiteIcons(sourceSiteLinks(item))\n','')
    s=once(s,'    val historyLoadMutex=remember{ kotlinx.coroutines.sync.Mutex() }','''    val historyLoadMutex=remember{ kotlinx.coroutines.sync.Mutex() }
    val catalogRevisions=remember(api.server){mutableMapOf<String,String>()}
    val watchChecked=remember(api.server){mutableMapOf<String,Long>()}
    val watchedByEndpoint=remember(api.server){mutableMapOf<String,List<JSONObject>>()}
    val pickerState=remember(api.server,endpoint.endpointId){HomeHistoryPickerState()}
    val pickerListState=remember(api.server,endpoint.endpointId){androidx.compose.foundation.lazy.LazyListState()}''')
    a=s.index('    suspend fun loadHistoryForEndpoint():Boolean=historyLoadMutex.withLock{')
    b=s.index('    suspend fun confirmEndpointSelection',a)
    s=s[:a]+'''    suspend fun loadHistoryForEndpoint(forceWatch:Boolean=false):Boolean=historyLoadMutex.withLock{
        val server=historyOwner.first ?: return@withLock false
        if(currentHistoryOwner!=historyOwner || !active)return@withLock false
        val key=endpoint.endpointId
        historyLoading=true; historyPreview=null; setEndpointHistoryError("")
        try {
            val priorRevision=catalogRevisions[key]
            val unchanged=if(historyHydrated && priorRevision!=null){
                val probe=api.json("GET",savedVideoHistoryPath()+"?limit=1",expectedServer=server)
                homeHistoryRevisionMatches(probe,priorRevision,savedHistory.size,savedHistory.count{!it.optBoolean("hidden",false)})
            }else false
            if(currentHistoryOwner!=historyOwner || api.server!=server)return@withLock false
            var nextRevision:String?=null
            val catalog=if(unchanged)savedHistory else {
                val incoming=savedVideoHistoryItemsWithRetry(api,onPreview={p->
                    if(currentHistoryOwner==historyOwner && !historyHydrated)historyPreview=p
                },onRevision={nextRevision=it})
                if(currentHistoryOwner!=historyOwner || api.server!=server)return@withLock false
                homeReuseHistoryRows(savedHistory,canonicalHistoryItems(incoming))
            }
            if(!unchanged){
                setEndpointSavedHistory(catalog)
                if(nextRevision==null)catalogRevisions.remove(key) else catalogRevisions[key]=nextRevision!!
                val initial=homeReuseHistoryRows(history,homeLibraryWithWatch(historyItemsAfterRead(history,catalog,watchedByEndpoint[key],endpoint.adapterKind=="home_edge_tv"),watchedByEndpoint[key]))
                if(initial!==history)setEndpointHistory(initial)
                setEndpointHistoryHydrated(true)
                historyPreview=null
                if(historyOpen && historyPickerSnapshot!==history)historyPickerSnapshot=history
            }
            val now=android.os.SystemClock.elapsedRealtime()
            if(forceWatch || !unchanged || homeHistoryWatchRefreshDue(watchChecked[key],now)){
                val watched=try{homeCompleteWatchResponse(api.json("GET",mediaHistoryPath(key),expectedServer=server))}
                    catch(cancelled:CancellationException){throw cancelled}catch(_:Exception){null}
                if(currentHistoryOwner!=historyOwner || api.server!=server)return@withLock false
                if(watched!=null){watchedByEndpoint[key]=watched;watchChecked[key]=now}
                val latest=watchedByEndpoint[key]
                val enriched=homeReuseHistoryRows(history,homeLibraryWithWatch(historyItemsAfterRead(history,catalog,latest,endpoint.adapterKind=="home_edge_tv"),latest))
                if(enriched!==history)setEndpointHistory(enriched)
                if(historyOpen && historyPickerSnapshot!==history)historyPickerSnapshot=history
            }
            true
        }catch(cancelled:CancellationException){throw cancelled}
        catch(e:Exception){
            if(currentHistoryOwner==historyOwner)setEndpointHistoryError(e.message?:"Спільний каталог недоступний")
            false
        }finally{
            if(currentHistoryOwner==historyOwner){historyLoading=false;historyPreview=null}
        }
    }
'''+s[b:]
    s=once(s,'if(api.server!=null&&active)loadHistoryForEndpoint()','if(api.server!=null&&active)loadHistoryForEndpoint(forceWatch=true)')
    s=once(s,'historyPickerSnapshot=history.toList()\n                        historyOpen=true','historyPickerSnapshot=history\n                        historyOpen=true')
    s=once(s,'HistoryPickerDialog(api,historyPickerSnapshot,historyPreview,historyLoading,historyError,historyHydrated,{','HistoryPickerDialog(api,historyPickerSnapshot,historyPreview,historyLoading,historyError,historyHydrated,pickerState,pickerListState,{')
    return s
