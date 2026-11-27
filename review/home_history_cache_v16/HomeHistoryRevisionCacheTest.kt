package com.skeleton.home
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
class HomeHistoryRevisionCacheTest {
    private val rev="a".repeat(64)
    private fun probe(r:String=rev,total:Int=2,visible:Int=1)=JSONObject()
        .put("page",JSONObject().put("revision",r).put("offset",0).put("matching",total).put("restart_required",false))
        .put("counts",JSONObject().put("total",total).put("visible",visible).put("hidden",total-visible))
        .put("items",JSONArray().apply{if(total>0)put(JSONObject().put("history_id","a"))})
    @Test fun unchangedRevisionSkipsFullFetch(){assertTrue(homeHistoryRevisionMatches(probe(),rev,2,1))}
    @Test fun changedRevisionOrCountRequiresFetch(){assertFalse(homeHistoryRevisionMatches(probe("b".repeat(64)),rev,2,1));assertFalse(homeHistoryRevisionMatches(probe(),rev,2,2))}
    @Test fun missingOrRestartPageCannotReuse(){assertFalse(homeHistoryRevisionMatches(JSONObject(),rev,2,1));val p=probe();p.getJSONObject("page").put("restart_required",true);assertFalse(homeHistoryRevisionMatches(p,rev,2,1))}
    @Test fun emptyCatalogIsComplete(){assertTrue(homeHistoryRevisionMatches(probe(total=0,visible=0),rev,0,0))}
    @Test fun unchangedRowsKeepIdentity(){val a=JSONObject().put("history_id","a");val old=listOf(a);assertSame(old,homeReuseHistoryRows(old,listOf(JSONObject(a.toString()))))}
    @Test fun changedHiddenAndDeletedRowsAreNotRetained(){val a=JSONObject().put("history_id","a");val b=JSONObject().put("history_id","b");val c=JSONObject(a.toString()).put("hidden",true);val next=homeReuseHistoryRows(listOf(a,b),listOf(c));assertEquals(1,next.size);assertSame(c,next[0]);assertTrue(next[0].getBoolean("hidden"))}
    @Test fun watchRefreshIsThrottledAndMonotonic(){assertTrue(homeHistoryWatchRefreshDue(null,10));assertFalse(homeHistoryWatchRefreshDue(10,29999));assertTrue(homeHistoryWatchRefreshDue(10,30010));assertTrue(homeHistoryWatchRefreshDue(10,9))}
}
