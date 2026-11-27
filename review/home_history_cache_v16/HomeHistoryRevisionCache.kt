package com.skeleton.home

import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import org.json.JSONObject

internal class HomeHistoryPickerState {
    var query by mutableStateOf("")
    var filters by mutableStateOf(HomeLibraryFilters())
}
internal fun homeHistoryRevisionMatches(reply:JSONObject,revision:String,total:Int,visible:Int):Boolean{
    val page=reply.optJSONObject("page") ?: return false
    val counts=reply.optJSONObject("counts") ?: return false
    return revision.matches(Regex("[0-9a-f]{64}")) &&
        page.optString("revision")==revision && !page.optBoolean("restart_required",true) &&
        page.optInt("offset",-1)==0 && page.optInt("matching",-1)==total &&
        counts.optInt("total",-1)==total && counts.optInt("visible",-1)==visible &&
        counts.optInt("hidden",-1)==total-visible &&
        reply.optJSONArray("items")?.length()==minOf(total,1)
}
internal fun homeHistoryWatchRefreshDue(last:Long?,now:Long):Boolean =
    last==null || now<last || now-last>=30_000L
internal fun homeReuseHistoryRows(previous:List<JSONObject>,incoming:List<JSONObject>):List<JSONObject>{
    val old=previous.filter{historyKey(it).isNotBlank()}.associateBy(::historyKey)
    val next=incoming.map{item->
        val prior=old[historyKey(item)]
        if(prior!=null && prior.toString()==item.toString())prior else item
    }
    return if(previous.size==next.size && previous.indices.all{previous[it]===next[it]})previous else next
}
