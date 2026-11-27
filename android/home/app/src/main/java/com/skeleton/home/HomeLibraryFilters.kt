package com.skeleton.home

import java.text.Collator
import java.util.Locale
import org.json.JSONObject

internal data class HomeLibraryFilters(
    val kinds:Set<String> = emptySet(), val sources:Set<String> = emptySet(),
    val genres:Set<String> = emptySet(), val languages:Set<String> = emptySet(),
    val watched:Set<String> = emptySet(), val updates:Set<String> = emptySet(),
    val yearFrom:String="", val yearTo:String="", val runtimeFrom:String="", val runtimeTo:String="",
    val imdbFrom:String="", val tmdbFrom:String="", val sort:String="recent"
){
    val count:Int get()=listOf(kinds,sources,genres,languages,watched,updates).count{it.isNotEmpty()}+
        listOf(yearFrom,yearTo,runtimeFrom,runtimeTo,imdbFrom,tmdbFrom).count{it.isNotBlank()}+(if(sort!="recent")1 else 0)
    val active:Boolean get()=count>0
}
internal val homeLibraryKinds=listOf(
    "movie" to "Фільм","series" to "Серіал","animation_movie" to "Мультфільм",
    "animation_series" to "Мультсеріал","anime_movie" to "Аніме-фільм","anime_series" to "Аніме-серіал",
    "documentary_movie" to "Документальний фільм","documentary_series" to "Документальний серіал","unknown" to "Інше"
)
internal val homeLibrarySorts=listOf(
    "recent" to "Остання активність ↓","oldest" to "Активність ↑","title_asc" to "Назва А–Я","title_desc" to "Назва Я–А",
    "year_desc" to "Рік ↓","year_asc" to "Рік ↑","imdb_desc" to "IMDb ↓","imdb_asc" to "IMDb ↑",
    "tmdb_desc" to "TMDB ↓","tmdb_asc" to "TMDB ↑","runtime_desc" to "Тривалість ↓","runtime_asc" to "Тривалість ↑"
)
internal val homeGenreLabels=mapOf("action" to "Бойовик","adventure" to "Пригоди","animation" to "Анімація","comedy" to "Комедія","crime" to "Кримінал","documentary" to "Документальний","drama" to "Драма","family" to "Сімейний","fantasy" to "Фентезі","history" to "Історичний","horror" to "Жахи","music" to "Музика","mystery" to "Детектив","romance" to "Мелодрама","science fiction" to "Фантастика","sci-fi & fantasy" to "Фантастика й фентезі","thriller" to "Трилер","war" to "Воєнний","western" to "Вестерн","unknown" to "Невідомо")
internal fun homeToggleFilter(values:Set<String>,key:String):Set<String> = if(key in values)values-key else values+key
internal fun homeLibraryKind(item:JSONObject):String{
    val raw=item.optString("content_type").lowercase(Locale.ROOT)
    if(raw.isNotBlank())return raw.takeIf{kind->homeLibraryKinds.any{it.first==kind}}?:"unknown"
    return when(item.optString("media_type").lowercase(Locale.ROOT)){"movie"->"movie";"tv"->"series";else->"unknown"}
}
internal fun homeLibrarySource(item:JSONObject):String{
    val state=item.optString("source_state")
    return when{
        state in setOf("full_release","trailer_only","no_source")->state
        item.optBoolean("trailer_only",false)->"trailer_only"
        item.optInt("full_source_count",0)>0->"full_release"
        else->"unknown"
    }
}
internal fun homeMetadataSet(item:JSONObject,field:String):Set<String>{
    val a=item.optJSONArray(field) ?: return setOf("unknown")
    return (0 until a.length()).mapNotNull{i->(a.opt(i) as? String)?.trim()?.lowercase(Locale.ROOT)?.takeIf{it.isNotBlank()}}.toSet().ifEmpty{setOf("unknown")}
}
internal fun homeLibraryWatch(item:JSONObject):String= item.optString("library_watch_state").takeIf{it in setOf("unwatched","started","last_completed")} ?: "unknown"
internal fun homeLibraryUpdate(item:JSONObject):String=item.optString("library_update_state").takeIf{it in setOf("awaiting_full_source","pending_release","monitored","off")} ?: "unknown"
internal fun homeCompleteWatchResponse(reply:JSONObject):List<JSONObject>?{
    val rows=reply.optJSONArray("items") ?: return null
    val counts=reply.optJSONObject("counts") ?: return null
    if(counts.optInt("total",-1)!=rows.length())return null
    return (0 until rows.length()).mapNotNull{rows.optJSONObject(it)}.takeIf{it.size==rows.length()}
}
internal fun homeLibraryWithWatch(items:List<JSONObject>,watched:List<JSONObject>?):List<JSONObject>{
    val index=watched?.filter{historyKey(it).isNotBlank()}?.associateBy(::historyKey)
    return items.map{item->
        val row=index?.get(historyKey(item))
        val state=when{
            index==null||historyKey(item).isBlank()->"unknown"
            row==null->"unwatched"
            row.optBoolean("completed",false)->"last_completed"
            row.optDouble("position_seconds",0.0)>0||row.optDouble("last_observed_position_seconds",0.0)>0->"started"
            else->"unknown"
        }
        JSONObject(item.toString()).put("library_watch_state",state)
    }
}
private fun homeNumber(item:JSONObject,key:String,max:Double=Double.MAX_VALUE):Double?=
    item.optDouble(key,Double.NaN).takeIf{it.isFinite()&&it>0&&it<=max}
private fun homeRange(value:Double?,lo:String,hi:String):Boolean{
    if(lo.isBlank()&&hi.isBlank())return true
    val min=lo.replace(',','.').toDoubleOrNull()
    val max=hi.replace(',','.').toDoubleOrNull()
    if((lo.isNotBlank()&&min==null)||(hi.isNotBlank()&&max==null))return false
    return value!=null&&(min==null||value>=min)&&(max==null||value<=max)
}
private fun homeMatches(selected:Set<String>,actual:Set<String>)=selected.isEmpty()||selected.any{it in actual}
private fun homeSortedHistory(items:List<JSONObject>,sort:String):List<JSONObject>{
    if(sort=="recent")return items
    val collator=Collator.getInstance(Locale.forLanguageTag("uk-UA"))
    fun number(x:JSONObject):Double?=when{
        sort=="oldest"->maxOf(x.optDouble("added_at",0.0),x.optDouble("last_viewed_at",0.0)).takeIf{it>0&&it.isFinite()}
        sort.startsWith("year_")->homeNumber(x,"year")
        sort.startsWith("runtime_")->homeNumber(x,"runtime_minutes")
        sort.startsWith("imdb_")->homeNumber(x,"imdb_rating",10.0)
        sort.startsWith("tmdb_")->homeNumber(x,"tmdb_rating_percent",100.0)
        else->null
    }
    return items.sortedWith(Comparator{a,b->
        if(sort.startsWith("title_")){
            val x=a.optString("title").trim();val y=b.optString("title").trim()
            when{ x.isBlank()&&y.isNotBlank()->1;y.isBlank()&&x.isNotBlank()->-1;else->collator.compare(x,y)*(if(sort.endsWith("_desc"))-1 else 1)}
        }else{
            val x=number(a);val y=number(b)
            when{x==null&&y==null->0;x==null->1;y==null->-1;sort.endsWith("_desc")->y.compareTo(x);else->x.compareTo(y)}
        }
    })
}
internal fun homeFilteredHistory(items:List<JSONObject>,query:String,filters:HomeLibraryFilters,includeHidden:Boolean):List<JSONObject>{
    val q=query.trim().lowercase(Locale.ROOT)
    val result=canonicalHistoryItems(items).filter{item->
        (includeHidden||!item.optBoolean("hidden",false)) &&
        homeMatches(filters.kinds,setOf(homeLibraryKind(item))) && homeMatches(filters.sources,setOf(homeLibrarySource(item))) &&
        homeMatches(filters.genres,homeMetadataSet(item,"genres")) && homeMatches(filters.languages,homeMetadataSet(item,"audio_languages")) &&
        homeMatches(filters.watched,setOf(homeLibraryWatch(item))) && homeMatches(filters.updates,setOf(homeLibraryUpdate(item))) &&
        homeRange(homeNumber(item,"year"),filters.yearFrom,filters.yearTo) &&
        homeRange(homeNumber(item,"runtime_minutes"),filters.runtimeFrom,filters.runtimeTo) &&
        homeRange(homeNumber(item,"imdb_rating",10.0),filters.imdbFrom,"") &&
        homeRange(homeNumber(item,"tmdb_rating_percent",100.0),filters.tmdbFrom,"") &&
        (q.isBlank()||listOf("title","original_title","year","content_type","overview").any{item.optString(it).lowercase(Locale.ROOT).contains(q)})
    }
    return homeSortedHistory(result,filters.sort)
}
