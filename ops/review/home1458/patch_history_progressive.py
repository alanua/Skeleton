"""Progressive picker, complete snapshots only in cache."""
import hashlib
BASE="d35d9e470e5e9150ca4f004369f01cb23df453d44a2060e39bf246c341110137"
def once(s,a,b):
    if s.count(a)!=1:raise ValueError("ANCHOR_MISMATCH: "+a[:100])
    return s.replace(a,b,1)
def patch(s):
    if hashlib.sha256(s.encode()).hexdigest()!=BASE:raise ValueError("BASE_MISMATCH")
    s=once(s,'private suspend fun savedVideoHistoryItemsPaged(api:HomeApi):List<JSONObject>{','''private suspend fun savedVideoHistoryItemsPaged(api:HomeApi,onPreview:(HomeHistoryPreview?)->Unit):List<JSONObject>{
    val ownerServer=api.server ?: throw IllegalStateException("Home Edge недоступний")
    var nextPreviewSize=100''')
    s=once(s,'        val response=api.get(path)\n        currentCoroutineContext().ensureActive()\n        val rows=response.optJSONArray("items")?.objects().orEmpty()', '''        val response=api.json("GET",path,expectedServer=ownerServer)
        currentCoroutineContext().ensureActive()
        if(api.server!=ownerServer)throw CancellationException("Сервер змінився")
        val rows=response.optJSONArray("items")?.objects().orEmpty()''')
    s=once(s,'        collected.addAll(rows)\n        if(next==null){','''        collected.addAll(rows)
        if(next!=null && collected.size>=nextPreviewSize){
            onPreview(HomeHistoryPreview(canonicalHistoryItems(collected),total,visible))
            nextPreviewSize=(collected.size.toLong()*2).coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
        }
        if(next==null){''')
    s=once(s,'private suspend fun savedVideoHistoryItemsWithRetry(api:HomeApi,attempts:Int=2):List<JSONObject>{','private suspend fun savedVideoHistoryItemsWithRetry(api:HomeApi,attempts:Int=2,onPreview:(HomeHistoryPreview?)->Unit={}):List<JSONObject>{')
    s=once(s,'        val result=runCatching{savedVideoHistoryItemsPaged(api)}','''        onPreview(null)
        val result=runCatching{savedVideoHistoryItemsPaged(api,onPreview)}''')
    s=once(s,'        lastError=result.exceptionOrNull()\n        if(i<attempts-1)delay(650)','        onPreview(null)\n        lastError=result.exceptionOrNull()\n        if(i<attempts-1)delay(650)')
    s=once(s,'@Composable private fun HistoryPickerDialog(api:HomeApi,items:List<JSONObject>,onAtlas:', '@Composable private fun HistoryPickerDialog(api:HomeApi,items:List<JSONObject>,preview:HomeHistoryPreview?,loading:Boolean,error:String,complete:Boolean,onAtlas:')
    s=once(s,'    val canonical=remember(items){canonicalHistoryItems(items)}\n    val total=canonical.size\n    val visibleCount=remember(canonical){canonical.count{!it.optBoolean("hidden",false)}}','''    val shown=preview?.items ?: items
    val canonical=remember(shown){canonicalHistoryItems(shown)}
    val total=preview?.total ?: canonical.size
    val visibleCount=preview?.visible ?: remember(canonical){canonical.count{!it.optBoolean("hidden",false)}}''')
    a=s.index('    val shown=preview?.items ?: items')
    b=s.index('@Composable private fun HistoryPickerRow',a)
    part=s[a:b]
    part=once(part,'Text("Загалом/видимі · знайдено '+chr(36)+'{visible.size}"','Text(homeHistoryResultLabel(visible.size,complete,loading,error)')
    part=once(part,'Text("$total/$visibleCount",','Text(if(complete||preview!=null)"$total/$visibleCount" else "—/—",')
    part=once(part,'                HomeLibraryFilterPanel(filters,expanded,canonical,{filters=it},{filters=HomeLibraryFilters();query=""},onAtlas)','''                HomeLibraryFilterPanel(filters,expanded,canonical,{filters=it},{filters=HomeLibraryFilters();query=""},onAtlas)
                if(loading)LinearProgressIndicator(Modifier.fillMaxWidth().height(2.dp),color=Accent)
                if(error.isNotBlank())Text(error,fontSize=11.sp,color=Color(0xFFFF8A80))''')
    s=s[:a]+part+s[b:]
    s=once(s,'val historyByEndpoint=remember{mutableStateMapOf<String,List<JSONObject>>()}; val savedHistoryByEndpoint=remember{mutableStateMapOf<String,List<JSONObject>>()}; val historyHydratedByEndpoint=remember{mutableStateMapOf<String,Boolean>()}; val historyErrorByEndpoint=remember{mutableStateMapOf<String,String>()}','val historyByEndpoint=remember(api.server){mutableStateMapOf<String,List<JSONObject>>()}; val savedHistoryByEndpoint=remember(api.server){mutableStateMapOf<String,List<JSONObject>>()}; val historyHydratedByEndpoint=remember(api.server){mutableStateMapOf<String,Boolean>()}; val historyErrorByEndpoint=remember(api.server){mutableStateMapOf<String,String>()}')
    a=s.index('    var history by remember(endpoint.endpointId)')
    b=s.index('\n',a)
    line=s[a:b].replace('remember(endpoint.endpointId)','remember(api.server,endpoint.endpointId)')
    line=once(line,'var historyPickerSnapshot by remember(api.server,endpoint.endpointId){mutableStateOf(emptyList<JSONObject>())};','var historyPickerSnapshot by remember(api.server,endpoint.endpointId){mutableStateOf(emptyList<JSONObject>())}; var historyPreview by remember(api.server,endpoint.endpointId){mutableStateOf<HomeHistoryPreview?>(null)};')
    s=s[:a]+line+s[b:]
    s=once(s,'    // Serialize overlapping manual/entry reads so editor return always gets a fresh read.','''    val currentHistoryOwner by rememberUpdatedState(Triple(api.server,endpoint.endpointId,active))
    val historyOwner=Triple(api.server,endpoint.endpointId,active)
    // Serialize overlapping manual/entry reads so editor return always gets a fresh read.''')
    s=once(s,'        if(api.server==null)return@withLock false\n        historyLoading=true; setEndpointHistoryError("")','''        if(api.server==null || currentHistoryOwner!=historyOwner || !active)return@withLock false
        historyLoading=true; historyPreview=null; setEndpointHistoryError("")''')
    s=once(s,'            val saved=runCatching{savedVideoHistoryItemsWithRetry(api)}','''            val saved=runCatching{savedVideoHistoryItemsWithRetry(api,onPreview={p->
                if(currentHistoryOwner==historyOwner && !historyHydrated)historyPreview=p
            })}''')
    s=once(s,'            val watched=runCatching{homeCompleteWatchResponse(api.get(mediaHistoryPath(endpoint.endpointId)))}','''            if(currentHistoryOwner!=historyOwner || api.server!=historyOwner.first)return@withLock false
            if(saved.isSuccess&&!historyHydrated){
                val completeCatalog=canonicalHistoryItems(saved.getOrThrow())
                setEndpointSavedHistory(completeCatalog)
                setEndpointHistory(completeCatalog)
                setEndpointHistoryHydrated(true)
                historyPreview=null
                if(historyOpen)historyPickerSnapshot=completeCatalog
            }
            val watched=if(saved.isSuccess)runCatching{homeCompleteWatchResponse(api.json("GET",mediaHistoryPath(endpoint.endpointId),expectedServer=historyOwner.first))} else Result.success(null)''')
    s=once(s,'            watched.exceptionOrNull()?.let{if(it is kotlinx.coroutines.CancellationException)throw it}\n            if(saved.isSuccess){','''            watched.exceptionOrNull()?.let{if(it is kotlinx.coroutines.CancellationException)throw it}
            if(currentHistoryOwner!=historyOwner || api.server!=historyOwner.first)return@withLock false
            if(saved.isSuccess){''')
    s=once(s,'                setEndpointHistoryHydrated(true)\n            }else{','''                setEndpointHistoryHydrated(true)
                if(historyOpen)historyPickerSnapshot=history.toList()
            }else{''')
    s=once(s,'        } finally {\n            historyLoading=false\n        }\n    }\n    suspend fun confirmEndpointSelection','''        } finally {
            historyLoading=false
            historyPreview=null
        }
    }
    suspend fun confirmEndpointSelection''')
    a=s.index('                    val historyActionable=historyPickerCanOpen(')
    b=s.index('                    if(isSeries){',a)
    s=s[:a]+'''                    val historyActionable=api.server!=null
                    HistoryPickerField(historyLabel,historyActionable,historyLoading&&!historyHydrated,historyError.isNotBlank()){
                        historyPickerSnapshot=history.toList()
                        historyOpen=true
                        if(shouldRefreshHistoryOnOpen(api.server!=null,historyLoading))scope.launch{
                            loadHistoryForEndpoint()
                        }
                    }
'''+s[b:]
    s=once(s,'HistoryPickerDialog(api,historyPickerSnapshot,{historyOpen=false;openMovieAtlas()}','HistoryPickerDialog(api,historyPickerSnapshot,historyPreview,historyLoading,historyError,historyHydrated,{historyOpen=false;openMovieAtlas()}')
    return s
