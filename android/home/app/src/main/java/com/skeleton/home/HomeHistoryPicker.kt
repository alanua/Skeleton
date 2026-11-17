package com.skeleton.home

import java.util.Locale
import org.json.JSONObject

internal data class HomeHistoryPickerResult(val items:List<JSONObject>,val total:Int)

/** Normal history and its counter are projected from the same visible canonical set. */
internal fun homeHistoryPickerResult(items:List<JSONObject>,query:String):HomeHistoryPickerResult{
    val canonical=visibleHistoryItems(items)
    val q=query.trim().lowercase(Locale.ROOT)
    val visible=if(q.isBlank())canonical else canonical.filter{x->
        listOf(x.optString("title"),x.optString("year"),x.optString("content_type"),x.optString("overview"))
            .joinToString(" ").lowercase(Locale.ROOT).contains(q)
    }
    return HomeHistoryPickerResult(visible,canonical.size)
}
