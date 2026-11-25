"""Exact-source Home history filters and collapsed Atlas entry; source only."""
import hashlib

BASE="5d6b1c888c370ee00ce001b701a25f0b2f6d9574002d81bb6ee8be477a77ee34"

def once(s,a,b):
    if s.count(a)!=1:raise ValueError("FILTER_ANCHOR_MISMATCH: "+a[:100])
    return s.replace(a,b,1)

def patch(source,atlas_transform):
    if hashlib.sha256(source.encode()).hexdigest()!=BASE:raise ValueError("FILTER_BASE_SHA_MISMATCH")
    s=atlas_transform(source)
    # One capability-gated Atlas entry, inside the collapsible history filter panel.
    s=once(s,'''        val openMovieAtlas = movieAtlasEntry(
            api, active, endpointId.takeIf { selected -> endpoints.any { it.endpointId == selected } }
        )
        LazyColumn(Modifier.weight(1f).padding(horizontal=13.dp),contentPadding=PaddingValues(top=4.dp,bottom=18.dp)){
            item {
                OutlinedButton(
                    onClick = openMovieAtlas,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
                ) { Text("Кіноатлас") }
            }
''','''        LazyColumn(Modifier.weight(1f).padding(horizontal=13.dp),contentPadding=PaddingValues(top=4.dp,bottom=18.dp)){
''')
    s=once(s,'    if(active&&historyOpen)HistoryPickerDialog(api,historyPickerSnapshot,{item->','''    val openMovieAtlas = movieAtlasEntry(api,active,endpointId.takeIf{selected->endpoints.any{it.endpointId==selected}})
    if(active&&historyOpen)HistoryPickerDialog(api,historyPickerSnapshot,{historyOpen=false;openMovieAtlas()},{item->''')
    s=once(s,'fun HistoryPickerDialog(api:HomeApi,items:List<JSONObject>,onSelect:', 'fun HistoryPickerDialog(api:HomeApi,items:List<JSONObject>,onAtlas:()->Unit,onSelect:')
    s=once(s,'''    var query by remember{mutableStateOf("")};val result=remember(items,query){homeHistoryPickerResult(items,query)};val q=query.trim().lowercase(Locale.ROOT);val visible=result.items''','''    var query by remember{mutableStateOf("")}
    var filters by remember{mutableStateOf(HomeLibraryFilters())}
    var expanded by remember{mutableStateOf(false)}
    val canonical=remember(items){canonicalHistoryItems(items)}
    val total=canonical.size
    val visibleCount=remember(canonical){canonical.count{!it.optBoolean("hidden",false)}}
    val visible=remember(canonical,query,filters){homeFilteredHistory(canonical,query,filters,includeHidden=false)}''')
    s=once(s,'''                Row(Modifier.fillMaxWidth().padding(horizontal=2.dp,vertical=6.dp)){Text(ui("Останні додані або переглянуті — першими"),fontSize=10.sp,color=Muted);Spacer(Modifier.weight(1f));Text(if(q.isBlank())"\${result.total}" else "\${visible.size} з \${result.total}",fontSize=10.sp,fontWeight=FontWeight.Bold,color=Accent)}'''.replace('\\$','$'),'''                HomeLibraryFilterPanel(filters,expanded,{expanded=!expanded},{filters=it},{filters=HomeLibraryFilters();query=""},onAtlas)
                Row(Modifier.fillMaxWidth().padding(horizontal=2.dp,vertical=6.dp)){Text("Загалом/видимі · знайдено \${visible.size}",fontSize=10.sp,color=Muted,modifier=Modifier.weight(1f));Text("$total/$visibleCount",fontSize=10.sp,fontWeight=FontWeight.Bold,color=Accent)}'''.replace('\\$','$'))
    s=once(s,'''var visibilityFilter by remember{mutableStateOf("all")};val focusRequester''','''var visibilityFilter by remember{mutableStateOf("all")};var filters by remember{mutableStateOf(HomeLibraryFilters())};var expanded by remember{mutableStateOf(false)};val focusRequester''')
    # Keep prior verified list visible during refresh, and never label a failed load as complete.
    s=once(s,'''    suspend fun reload(){if(api.server==null)return;busy=true;runCatching{canonicalHistoryItems(savedVideoHistoryItemsWithRetry(api))}.onSuccess{items=it;error=""}.onFailure{error=it.message?:"Спільний каталог недоступний"};busy=false}''','''    suspend fun reload(){
        if(api.server==null)return
        busy=true
        try{items=canonicalHistoryItems(savedVideoHistoryItemsWithRetry(api));error=""}
        catch(cancelled:CancellationException){throw cancelled}
        catch(e:Exception){error=e.message?:"Спільний каталог недоступний"}
        finally{busy=false}
    }''')
    s=once(s,'''    val visible=items.filter{ x -> (visibilityFilter=="all" || (visibilityFilter=="hidden")==x.optBoolean("hidden",false)) && (q.isBlank() || listOf(x.optString("title"),x.optString("year"),x.optString("content_type"),x.optString("overview")).joinToString(" ").lowercase(Locale.ROOT).contains(q)) }''','''    val visible=remember(items,query,filters,visibilityFilter){homeFilteredHistory(items,query,filters,includeHidden=true).filter{x->visibilityFilter=="all"||(visibilityFilter=="hidden")==x.optBoolean("hidden",false)}}''')
    old='''                     Row(Modifier.fillMaxWidth().padding(horizontal=2.dp,vertical=6.dp),verticalAlignment=Alignment.CenterVertically){Text(ui("Спільний каталог творів · перегляди окремі для пристроїв"),fontSize=10.sp,color=Muted,modifier=Modifier.weight(1f));Text(if(q.isBlank())"$totalCount/$visibleCount" else "\${visible.size} з $totalCount/$visibleCount",fontSize=10.sp,fontWeight=FontWeight.Bold,color=Accent)}'''.replace('\\$','$')
    s=once(s,old.strip(),'''HomeLibraryFilterPanel(filters,expanded,{expanded=!expanded},{filters=it},{filters=HomeLibraryFilters();query="";visibilityFilter="all"},null)
                     Row(Modifier.fillMaxWidth().padding(horizontal=2.dp,vertical=6.dp),verticalAlignment=Alignment.CenterVertically){Text(if(busy)"Оновлення · знайдено \${visible.size}" else "Загалом/видимі · знайдено \${visible.size}",fontSize=10.sp,color=Muted,modifier=Modifier.weight(1f));Text(if(error.isNotBlank()&&items.isEmpty())"—/—" else "$totalCount/$visibleCount",fontSize=10.sp,fontWeight=FontWeight.Bold,color=Accent)}'''.replace('\\$','$'))
    s=once(s,'''                if(busy) Box(Modifier.fillMaxSize(),contentAlignment=Alignment.Center){CircularProgressIndicator(color=Accent,strokeWidth=2.dp,modifier=Modifier.size(28.dp))}''','''                if(busy&&items.isEmpty()) Box(Modifier.fillMaxSize(),contentAlignment=Alignment.Center){CircularProgressIndicator(color=Accent,strokeWidth=2.dp,modifier=Modifier.size(28.dp))}''')
    anchor='@Composable private fun HistoryPickerDialog('
    panel='''@Composable
private fun HomeLibraryFilterPanel(filters:HomeLibraryFilters,expanded:Boolean,onToggle:()->Unit,onChange:(HomeLibraryFilters)->Unit,onReset:()->Unit,onAtlas:(()->Unit)?){
    Row(Modifier.fillMaxWidth().heightIn(min=36.dp).clickable(onClick=onToggle).padding(horizontal=3.dp),verticalAlignment=Alignment.CenterVertically){
        Text(if(filters.active)"Фільтри · активні" else "Фільтри",fontSize=12.sp,color=if(filters.active)Accent else Muted,modifier=Modifier.weight(1f))
        Mdi(if(expanded)"chevron-up" else "chevron-down",18.dp,Muted)
    }
    if(expanded){
        Column(Modifier.fillMaxWidth().padding(bottom=5.dp),verticalArrangement=Arrangement.spacedBy(3.dp)){
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(6.dp)){
                homeLibraryKinds.forEach{(kind,label)->FilterChip(selected=kind in filters.kinds,onClick={onChange(filters.copy(kinds=homeToggleFilter(filters.kinds,kind)))},label={Text(label,fontSize=11.sp)})}
            }
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(6.dp)){
                listOf("full_release" to "Є відео","no_source" to "Очікує джерело","trailer_only" to "Лише трейлер").forEach{(kind,label)->FilterChip(selected=kind in filters.sources,onClick={onChange(filters.copy(sources=homeToggleFilter(filters.sources,kind)))},label={Text(label,fontSize=11.sp)})}
            }
            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically){
                TextButton(onClick=onReset){Text("Скинути всі",fontSize=12.sp)}
                Spacer(Modifier.weight(1f))
                if(onAtlas!=null)IconButton(onClick=onAtlas,modifier=Modifier.size(48.dp)){Icon(painterResource(R.drawable.movie_atlas_map),contentDescription="Кіноатлас 3D · На ТВ",tint=Accent,modifier=Modifier.size(24.dp))}
            }
        }
    }
}

'''
    return once(s,anchor,panel+anchor)
