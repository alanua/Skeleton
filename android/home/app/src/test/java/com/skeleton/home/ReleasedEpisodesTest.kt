package com.skeleton.home

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ReleasedEpisodesTest {
    private fun data() = JSONObject("""{
      "episode_options": [
        {"season":"1","episode":"Серія 1","playback":"local_verified","source_count":4,"external_url":null},
        {"season":"1","episode":"Серія 2","playback":"official_external_only","source_count":0,"external_url":"https://www.ivi.tv/watch/reportazh-2035/690161"},
        {"season":"1","episode":"Серія 3","playback":"official_external_only","source_count":0,"external_url":"https://www.ivi.tv/watch/reportazh-2035/690451"},
        {"season":"1","episode":"Серія 4","playback":"official_external_only","source_count":0,"external_url":"https://www.ivi.tv/watch/reportazh-2035/690452"}
      ]
    }""")
    @Test fun allFourReleasedEntriesHaveDistinctLabels(){
        assertEquals(listOf("Серія 1","Серія 2","Серія 3","Серія 4"),
            homeVisibleEpisodeOptions(data(),"1",listOf("Серія 1")))
    }
    @Test fun onlyUnplayableEpisodesGetExternalUrl(){
        assertNull(homeExternalEpisodeUrl(data(),"1","Серія 1",true))
        assertNull(homeExternalEpisodeUrl(data(),"1","Серія 2",true))
        assertEquals("https://www.ivi.tv/watch/reportazh-2035/690161",homeExternalEpisodeUrl(data(),"1","Серія 2",false))
        assertEquals("https://www.ivi.tv/watch/reportazh-2035/690451",homeExternalEpisodeUrl(data(),"1","Серія 3",false))
        assertEquals("https://www.ivi.tv/watch/reportazh-2035/690452",homeExternalEpisodeUrl(data(),"1","Серія 4",false))
    }
    @Test fun rejectSpoofedOrNotYetPublishedEpisode(){
        val x=data()
        x.getJSONArray("episode_options").put(JSONObject()
            .put("season","1").put("episode","Серія 5").put("playback","upcoming")
            .put("url","https://www.ivi.tv/watch/reportazh-2035/690453"))
        x.getJSONArray("episode_options").put(JSONObject()
            .put("season","1").put("episode","Серія 6").put("playback","official_external_only")
            .put("external_url","https://other.example/evil"))
        assertEquals(6,homeVisibleEpisodeOptions(x,"1",listOf("Серія 1")).size)
        assertNull(homeExternalEpisodeUrl(x,"1","Серія 5",false))
        assertNull(homeExternalEpisodeUrl(x,"1","Серія 6",false))
    }
    @Test fun unrelatedSeriesNotAffected(){
        assertEquals(listOf("Серія 1"),homeVisibleEpisodeOptions(JSONObject(),"1",listOf("Серія 1")))
    }
}
