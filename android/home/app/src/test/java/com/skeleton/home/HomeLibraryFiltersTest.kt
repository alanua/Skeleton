package com.skeleton.home
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
class HomeLibraryFiltersTest{
    private fun row(id:String,kind:String,media:String="tv",hidden:Boolean=false,state:String="full_release")=JSONObject().put("history_id",id).put("title",id).put("content_type",kind).put("media_type",media).put("hidden",hidden).put("source_state",state)
    @Test fun emptyFiltersPreserveVisibleCanonicalOrder(){
        val a=row("a","series");val b=row("b","anime_series");val h=row("h","movie",hidden=true)
        assertEquals(listOf(a,b),homeFilteredHistory(listOf(a,b,h,a),"",HomeLibraryFilters(),false))
        assertEquals(listOf(a,b,h),homeFilteredHistory(listOf(a,b,h),"",HomeLibraryFilters(),true))
    }
    @Test fun multiTypeOrAndSourceIntersection(){
        val a=row("a","anime_series");val b=row("b","animation_movie",state="no_source");val c=row("c","series")
        assertEquals(listOf(b),homeFilteredHistory(listOf(a,b,c),"",HomeLibraryFilters(setOf("anime_series","animation_movie"),setOf("no_source")),true))
    }
    @Test fun unknownDoesNotInventAnime(){
        assertEquals("series",homeLibraryKind(row("a","")))
        assertEquals("unknown",homeLibraryKind(row("b","unverified_subtype")))
    }
    @Test fun originalTitleSearchAndReset(){
        val a=row("a","movie").put("original_title","Under Salt Marsh")
        assertEquals(listOf(a),homeFilteredHistory(listOf(a),"salt marsh",HomeLibraryFilters(),false))
        assertEquals(setOf("movie"),homeToggleFilter(setOf("movie","series"),"series"))
        assertFalse(HomeLibraryFilters().active)
    }
}
