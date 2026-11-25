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

    @Test fun combinedGenreAudioRangesAndRatings(){
        val a=row("a","series").put("genres",org.json.JSONArray(listOf("Drama","Crime"))).put("audio_languages",org.json.JSONArray(listOf("uk","ru"))).put("year",2020).put("runtime_minutes",48).put("imdb_rating",8.1).put("tmdb_rating_percent",80)
        val b=row("b","series").put("year",2020)
        val f=HomeLibraryFilters(genres=setOf("crime","comedy"),languages=setOf("uk"),yearFrom="2019",yearTo="2021",runtimeTo="60",imdbFrom="8",tmdbFrom="75")
        assertEquals(listOf(a),homeFilteredHistory(listOf(a,b),"",f,false))
        assertTrue(homeFilteredHistory(listOf(a),"",f.copy(yearFrom="2022"),false).isEmpty())
        assertEquals(listOf(b),homeFilteredHistory(listOf(a,b),"",HomeLibraryFilters(genres=setOf("unknown")),false))
    }
    @Test fun allNumericalSortsKeepUnknownLast(){
        val a=row("a","movie").put("year",2000).put("runtime_minutes",90).put("imdb_rating",6).put("tmdb_rating_percent",60)
        val b=row("b","movie").put("year",2020).put("runtime_minutes",120).put("imdb_rating",9).put("tmdb_rating_percent",90)
        val u=row("u","movie")
        for(k in listOf("year","runtime","imdb","tmdb")){
            assertEquals(listOf(a,b,u),homeFilteredHistory(listOf(u,b,a),"",HomeLibraryFilters(sort=k+"_asc"),false))
            assertEquals(listOf(b,a,u),homeFilteredHistory(listOf(u,a,b),"",HomeLibraryFilters(sort=k+"_desc"),false))
        }
    }
    @Test fun watchStateOnlyFromSelectedEndpoint(){
        val a=row("a","series");val b=row("b","series");val c=row("c","movie")
        val w=listOf(row("a","series").put("position_seconds",35),row("b","series").put("completed",true))
        val result=homeLibraryWithWatch(listOf(a,b,c),w)
        assertEquals(listOf("started","last_completed","unwatched"),result.map(::homeLibraryWatch))
        assertEquals("unknown",homeLibraryWatch(homeLibraryWithWatch(listOf(a),null).single()))
        assertFalse(a.has("library_watch_state"))
    }
    @Test fun pendingUpdateAndReset(){
        val a=row("a","series").put("library_update_state","pending_release")
        val b=row("b","series")
        assertEquals(listOf(a),homeFilteredHistory(listOf(a,b),"",HomeLibraryFilters(updates=setOf("pending_release")),false))
        assertEquals(listOf(a,b),homeFilteredHistory(listOf(a,b),"",HomeLibraryFilters(),false))
        assertEquals(0,HomeLibraryFilters().count)
    }
    @Test fun activityAndTitleSort(){
        val a=row("a","movie").put("added_at",10);val b=row("b","movie").put("last_viewed_at",20)
        assertEquals(listOf(a,b),homeFilteredHistory(listOf(b,a),"",HomeLibraryFilters(sort="oldest"),false))
        assertEquals(listOf(b,a),homeFilteredHistory(listOf(a,b),"",HomeLibraryFilters(sort="title_desc"),false))
        assertEquals(listOf(a,b),homeFilteredHistory(listOf(b,a),"",HomeLibraryFilters(sort="title_asc"),false))
    }

    @Test fun incompleteEndpointResponseDoesNotClaimUnwatched(){
        val rows=org.json.JSONArray(listOf(row("a","movie")))
        assertNull(homeCompleteWatchResponse(JSONObject().put("items",rows)))
        assertNull(homeCompleteWatchResponse(JSONObject().put("items",rows).put("counts",JSONObject().put("total",2))))
        assertEquals(1,homeCompleteWatchResponse(JSONObject().put("items",rows).put("counts",JSONObject().put("total",1)))!!.size)
    }
}
