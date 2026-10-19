package com.skeleton.home

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MediaHistoryCanonicalCardTest {
    @Test
    fun canonicalWorkKeyWinsOverLegacyHistoryIdAndContentKey() {
        val row = JSONObject()
            .put("history_id", "legacy-job-history")
            .put("content_key", "old-content-key")
            .put("history_key", "canonical-work-95480")
        assertEquals("canonical-work-95480", historyKey(row))
    }

    @Test
    fun missingOrBlankCanonicalKeyFallsBackWithoutFabricatingAnIdentity() {
        val fallback = JSONObject()
            .put("history_id", "legacy-job-history")
            .put("history_key", "  ")
            .put("content_key", "resolved-content-key")
        assertEquals("resolved-content-key", historyKey(fallback))
        assertEquals("legacy-job-history", historyKey(JSONObject().put("history_id", "legacy-job-history")))
        assertEquals("", historyKey(JSONObject()))
    }

    @Test
    fun twoLegacyEpisodeRowsMapToOneCanonicalCard() {
        val first = JSONObject()
            .put("history_id", "legacy-source-a")
            .put("history_key", "work-95480")
            .put("episode_key", "s6e4")
        val second = JSONObject()
            .put("history_id", "legacy-source-b")
            .put("history_key", "work-95480")
            .put("episode_key", "s6e5")
        val result = canonicalHistoryItems(listOf(first, second))
        assertEquals(1, result.size)
        assertEquals("work-95480", historyKey(result.single()))
    }

    @Test
    fun watchedProgressMergesWithSavedWorkDespiteDifferentLegacyHistoryIds() {
        val saved = JSONObject()
            .put("history_id", "work-95480")
            .put("title", "Canonical show")
        val watched = JSONObject()
            .put("history_id", "old-discovery-job")
            .put("history_key", "work-95480")
            .put("episode_key", "s6e5")
            .put("position_seconds", 123.5)
        val merged = historyItemsAfterRead(
            previous = emptyList(),
            savedLibrary = listOf(saved),
            watchedHistory = listOf(watched),
            homeEdgeTv = true
        )
        assertEquals(1, merged.size)
        assertEquals("work-95480", historyKey(merged.single()))
        assertEquals("Canonical show", merged.single().getString("title"))
        assertEquals(123.5, merged.single().getDouble("position_seconds"), 0.001)
    }

    @Test
    fun sameLegacyIdMustNeverJoinDifferentCanonicalWorks() {
        val first = JSONObject()
            .put("history_id", "old-shared-history")
            .put("history_key", "series-work-2026")
        val second = JSONObject()
            .put("history_id", "old-shared-history")
            .put("history_key", "movie-work-2008")
        val result = canonicalHistoryItems(listOf(first, second))
        assertEquals(2, result.size)
        assertTrue(result.map(::historyKey).containsAll(listOf("series-work-2026", "movie-work-2008")))
    }
}
