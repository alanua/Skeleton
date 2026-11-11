package com.skeleton.home

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeVideoSourceRefreshTest {
    private fun source(id:String, season:String, episode:String):JSONObject =
        JSONObject().put("source_id",id).put("season",season).put("episode",episode)
            .put("quality","960p").put("group","Цікава Ідея")

    private fun job(id:String, episodes:Int, status:String="ready"):JSONObject{
        val sources = mutableListOf(source("s1e1","1","Серія 1"))
        for(e in 1..episodes)sources.add(source("s2e$e","2","Серія $e"))
        return JSONObject()
            .put("job_id",id).put("status",status)
            .put("catalog",JSONObject().put("media_type","tv").put("seasons",JSONArray(listOf(
                JSONObject().put("season_number",1).put("loaded",true),
                JSONObject().put("season_number",2).put("loaded",true)
            ))))
            .put("sources",JSONArray(sources))
    }

    @Test fun newSeasonEpisodesRefreshWithoutChangingSelectedJob(){
        val previouslyVisible=job("dark-matter",1)
        val refreshed=job("dark-matter",7)
        val before=nativeVideoSourceRevision(previouslyVisible)

        assertEquals(2,before.size)
        assertEquals(8,nativeVideoSourceRevision(refreshed).size)
        assertTrue(shouldRefreshNativeVideoSources(previouslyVisible,refreshed,"dark-matter"))
        // Pure comparison: neither source set nor any remote player/selection is changed.
        assertEquals(before,nativeVideoSourceRevision(previouslyVisible))
        assertEquals(2,previouslyVisible.getJSONArray("sources").length())
    }

    @Test fun unchangedSourcesDoNotCauseRepeatedUiReload(){
        assertFalse(shouldRefreshNativeVideoSources(job("dark-matter",7),job("dark-matter",7),"dark-matter"))
    }

    @Test fun cannotInjectOtherWorkSourcesAcrossSelection(){
        assertFalse(shouldRefreshNativeVideoSources(job("dark-matter",1),job("other-job",7),"dark-matter"))
        assertFalse(shouldRefreshNativeVideoSources(job("dark-matter",1),job("dark-matter",7),"different-selection"))
    }

    @Test fun pendingAndIncompleteRefreshesDoNotReplaceVerifiedSources(){
        val old=job("dark-matter",1)
        assertFalse(shouldRefreshNativeVideoSources(old,job("dark-matter",7,"discovering"),"dark-matter"))
        val incomplete=job("dark-matter",1)
        incomplete.put("sources",JSONArray(listOf(source("s2e1","2","Серія 1"))))
        assertFalse(shouldRefreshNativeVideoSources(old,incomplete,"dark-matter"))
    }

    @Test fun sourceUrlRotationIsNotMistakenForAdditionalEpisodes(){
        val old=job("dark-matter",7)
        val current=job("dark-matter",7)
        current.getJSONArray("sources").getJSONObject(1).put("url","https://example.org/new-signed-stream")
        assertEquals(nativeVideoSourceRevision(old),nativeVideoSourceRevision(current))
        assertFalse(shouldRefreshNativeVideoSources(old,current,"dark-matter"))
    }
}
