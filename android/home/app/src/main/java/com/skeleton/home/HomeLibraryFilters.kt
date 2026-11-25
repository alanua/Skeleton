package com.skeleton.home

import java.util.Locale
import org.json.JSONObject

internal data class HomeLibraryFilters(val kinds:Set<String> = emptySet(),val sources:Set<String> = emptySet()){
    val active:Boolean get()=kinds.isNotEmpty()||sources.isNotEmpty()
}
internal val homeLibraryKinds=listOf(
    "movie" to "Фільм","series" to "Серіал","animation_movie" to "Мультфільм",
    "animation_series" to "Мультсеріал","anime_movie" to "Аніме-фільм","anime_series" to "Аніме-серіал",
    "documentary_movie" to "Документальний фільм","documentary_series" to "Документальний серіал","unknown" to "Інше"
)
internal fun homeToggleFilter(values:Set<String>,key:String):Set<String> =
    if(key in values)values-key else values+key
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
        else->"no_source"
    }
}
internal fun homeFilteredHistory(items:List<JSONObject>,query:String,filters:HomeLibraryFilters,includeHidden:Boolean):List<JSONObject>{
    val q=query.trim().lowercase(Locale.ROOT)
    return canonicalHistoryItems(items).filter{item->
        (includeHidden||!item.optBoolean("hidden",false)) &&
        (filters.kinds.isEmpty()||homeLibraryKind(item) in filters.kinds) &&
        (filters.sources.isEmpty()||homeLibrarySource(item) in filters.sources) &&
        (q.isBlank()||listOf("title","original_title","year","content_type","overview").any{item.optString(it).lowercase(Locale.ROOT).contains(q)})
    }
}
