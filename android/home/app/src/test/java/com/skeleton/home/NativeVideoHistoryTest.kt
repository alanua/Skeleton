package com.skeleton.home

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeVideoHistoryTest {
    @Test
    fun savedLibraryItemAbsentFromWatchedHistoryStaysVisible() {
        val saved = listOf(
            history("bb", "Babylon Berlin", poster = "/covers/babylon.jpg", overview = "Berlin, 1929"),
            history("pm", "Perry Mason", poster = "/covers/perry.jpg", overview = "Los Angeles")
        )
        val watched = listOf(history("pm", "Perry Mason", season = "2", episode = "1"))

        val merged = canonicalHistoryItems(mergedHistoryItems(saved, watched))

        assertEquals(listOf("Babylon Berlin", "Perry Mason"), merged.map { it.getString("title") })
        assertEquals("/covers/babylon.jpg", merged[0].getString("poster"))
        assertEquals("Los Angeles", merged[1].getString("overview"))
        assertEquals("2", merged[1].getString("season"))
    }

    @Test
    fun historyOpenUsesStableSavedIdsAndEndpointScopedSelection() {
        val item = history("bb", "Babylon Berlin").put("history_key", "work:bb").put("content_key", "tmdb:tv:66980")
        val body = savedHistoryOpenBody("home_edge_tv", item, "1")
        val selection = endpointSelectionFromHistoryOpen(
            "home_edge_tv",
            item,
            JSONObject()
                .put("job_id", "job-merged")
                .put("preferred", JSONObject().put("source_id", "s1e1-1080").put("season", "1").put("episode", "1").put("voice", "UA"))
        )

        assertEquals("bb", body.getString("history_id"))
        assertEquals("work:bb", body.getString("history_key"))
        assertEquals("home_edge_tv", body.getString("endpoint_id"))
        assertEquals("home_edge_tv", selection.getString("endpoint_id"))
        assertEquals("job-merged", selection.getString("job_id"))
        assertTrue(isCanonicalEndpointSelection(selection, "home_edge_tv"))
        assertFalse(isCanonicalEndpointSelection(selection, "samsung_tv"))
    }

    @Test
    fun mergedSeasonJobMustContainSourcesForAllLoadedSeasons() {
        val s2Only = job(
            loadedSeasons = listOf(1, 2),
            sources = listOf(source("s2e1", "2", "1"))
        )
        val merged = job(
            loadedSeasons = listOf(1, 2),
            sources = listOf(source("s1e1", "1", "1"), source("s2e1", "2", "1"))
        )

        assertFalse(mergedReadyJobHasLoadedSeasonSources(s2Only))
        assertTrue(mergedReadyJobHasLoadedSeasonSources(merged))
        assertEquals(setOf("1", "2"), sourceSeasons(merged.getJSONArray("sources").objects()))
    }

    @Test
    fun noFalseLoadedBadgeWhenMetadataHasNoSources() {
        val empty = job(loadedSeasons = listOf(1), sources = emptyList())

        assertEquals(setOf("1"), loadedMetadataSeasons(empty))
        assertTrue(sourceSeasons(empty.getJSONArray("sources").objects()).isEmpty())
        assertFalse(mergedReadyJobHasLoadedSeasonSources(empty))
    }

    @Test
    fun pollingIdentityRequiresCanonicalEndpointSelection() {
        val tv = JSONObject().put("endpoint_id", "home_edge_tv").put("job_id", "merged").put("source_id", "s1e1").put("season", "1")
        val samsung = JSONObject().put("endpoint_id", "samsung_tv").put("job_id", "watched").put("source_id", "safe")
        val incomplete = JSONObject().put("endpoint_id", "home_edge_tv").put("job_id", "old")

        assertTrue(isCanonicalEndpointSelection(tv, "home_edge_tv"))
        assertTrue(isCanonicalEndpointSelection(samsung, "samsung_tv"))
        assertFalse(isCanonicalEndpointSelection(samsung, "home_edge_tv"))
        assertFalse(isCanonicalEndpointSelection(incomplete, "home_edge_tv"))
        assertFalse(videoSelectionIdentityForTest(tv) == videoSelectionIdentityForTest(samsung))
        assertTrue(shouldApplyPolledVideoSelection(tv, "home_edge_tv", videoSelectionIdentityForTest(samsung), ""))
        assertFalse(shouldApplyPolledVideoSelection(tv, "home_edge_tv", videoSelectionIdentityForTest(samsung), videoSelectionIdentityForTest(tv)))
        assertFalse(shouldApplyPolledVideoSelection(incomplete, "home_edge_tv", "", ""))
    }

    @Test
    fun failedNetworkReadPreservesExistingHistoryAndSamsungWatchedScope() {
        val previous = listOf(history("bb", "Babylon Berlin"))
        val watched = listOf(history("pm", "Perry Mason"))
        val saved = listOf(history("bb", "Babylon Berlin"), history("pm-saved", "Perry Mason"))

        assertEquals(previous, historyItemsAfterRead(previous, null, null, homeEdgeTv = true))
        assertEquals(previous, historyItemsAfterRead(previous, saved, null, homeEdgeTv = false))
        assertEquals(listOf("Babylon Berlin", "Perry Mason"), historyItemsAfterRead(previous, null, watched, homeEdgeTv = true).map { it.getString("title") })

        val tvMerged = historyItemsAfterRead(previous, saved, watched, homeEdgeTv = true)
        val samsung = historyItemsAfterRead(previous, saved, watched, homeEdgeTv = false)

        assertEquals(listOf("Babylon Berlin", "Perry Mason"), tvMerged.map { it.getString("title") })
        assertEquals(listOf("Perry Mason"), samsung.map { it.getString("title") })
    }

    @Test
    fun canonicalSavedHistoryEndpointAndReadFallbacksAreStable() {
        val previous = listOf(history("bb", "Babylon Berlin"))
        val watched = listOf(history("pm", "Perry Mason", season = "1", episode = "3"))
        val saved = listOf(history("bb", "Babylon Berlin"), history("future", "Future Saved Work"))

        assertEquals("/api/video/history", savedVideoHistoryPath())
        assertEquals(listOf("Babylon Berlin", "Perry Mason"), historyItemsAfterRead(previous, null, watched, homeEdgeTv = true).map { it.getString("title") })
        assertEquals(listOf("Babylon Berlin", "Future Saved Work"), historyItemsAfterRead(previous, saved, null, homeEdgeTv = true).map { it.getString("title") })
    }

    @Test
    fun successfulSavedReadIsAuthoritativeOverStaleWatchedHistory() {
        val previous = listOf(history("bb", "Babylon Berlin"), history("deleted", "Deleted Saved Work"))
        val saved = listOf(history("bb", "Babylon Berlin"))
        val staleWatched = listOf(history("deleted", "Deleted Saved Work", season = "1", episode = "1"))

        val visible = historyItemsAfterRead(previous, saved, staleWatched, homeEdgeTv = true)

        assertEquals(listOf("Babylon Berlin"), visible.map { it.getString("title") })
    }

    @Test
    fun transientSavedReadFailureKeepsLastCompleteSavedList() {
        val previous = listOf(history("deleted", "Deleted Saved Work"))
        val lastCompleteSaved = listOf(history("bb", "Babylon Berlin"))
        val staleWatched = listOf(history("deleted", "Deleted Saved Work"), history("bb", "Babylon Berlin", season = "1", episode = "2"))

        val visible = historyItemsAfterRead(previous, null, staleWatched, homeEdgeTv = true, previousSavedLibrary = lastCompleteSaved)

        assertEquals(listOf("Babylon Berlin"), visible.map { it.getString("title") })
        assertEquals("1", visible[0].getString("season"))
        assertEquals("2", visible[0].getString("episode"))
    }

    @Test
    fun savedHistoryOpenCanonicalizesSourceAgainstMergedAllSeasonJob() {
        val sources = listOf(source("s1e1", "1", "1"), source("s2e1", "2", "1"), source("trailer", "", ""))
        sources[2].put("source_type", "trailer")
        val stale = JSONObject().put("source_id", "old-single-season").put("season", "2").put("episode", "1").put("voice", "UA")
        val exact = JSONObject().put("source_id", "s1e1").put("season", "1").put("episode", "1")

        assertEquals("s2e1", canonicalSourceForSelection(stale, sources)?.getString("source_id"))
        assertEquals("s1e1", canonicalSourceForSelection(exact, sources)?.getString("source_id"))
    }

    private fun history(id:String, title:String, poster:String = "", overview:String = "", season:String = "", episode:String = ""):JSONObject =
        JSONObject().put("history_id", id).put("title", title).put("poster", poster).put("overview", overview).put("season", season).put("episode", episode)

    private fun source(id:String, season:String, episode:String):JSONObject =
        JSONObject().put("source_id", id).put("season", season).put("episode", episode).put("quality", "1080p").put("translation", "UA")

    private fun job(loadedSeasons:List<Int>, sources:List<JSONObject>):JSONObject =
        JSONObject()
            .put("status", "ready")
            .put("catalog", JSONObject().put("media_type", "tv").put("seasons", JSONArray(loadedSeasons.map { JSONObject().put("season_number", it).put("loaded", true) })))
            .put("sources", JSONArray(sources))
}
