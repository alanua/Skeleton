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

    @Test
    fun deletedSharedWorkNeverResurrectsFromStaleEndpointWatchRows() {
        val deleted = JSONObject().put("history_id", "deleted-work-2026").put("title", "Removed series")
        val oldTvWatch = JSONObject()
            .put("history_id", "source-old-tv")
            .put("history_key", "deleted-work-2026")
            .put("position_seconds", 182.0)
        val forTv = historyItemsAfterRead(
            previous = listOf(deleted),
            savedLibrary = emptyList(),
            watchedHistory = listOf(oldTvWatch),
            homeEdgeTv = true,
            previousSavedLibrary = listOf(deleted)
        )
        val forTablet = historyItemsAfterRead(
            previous = listOf(deleted),
            savedLibrary = emptyList(),
            watchedHistory = listOf(oldTvWatch),
            homeEdgeTv = false,
            previousSavedLibrary = listOf(deleted)
        )
        assertTrue(forTv.isEmpty())
        assertTrue(forTablet.isEmpty())
    }

    @Test
    fun sharedWorkSetHasSeparateTvAndTabletWatchProgress() {
        val saved = listOf(
            JSONObject().put("history_id", "shared-work-a").put("title", "Show A"),
            JSONObject().put("history_id", "shared-work-b").put("title", "Show B")
        )
        val tvWatch = listOf(JSONObject().put("history_id", "tv-old-job")
            .put("history_key", "shared-work-a").put("position_seconds", 90.0))
        val tabletWatch = listOf(JSONObject().put("history_id", "tablet-old-job")
            .put("history_key", "shared-work-a").put("position_seconds", 210.0))
        val tv = historyItemsAfterRead(emptyList(), saved, tvWatch, true)
        val tablet = historyItemsAfterRead(emptyList(), saved, tabletWatch, false)
        assertEquals(listOf("shared-work-a", "shared-work-b"), tv.map(::historyKey))
        assertEquals(tv.map(::historyKey), tablet.map(::historyKey))
        assertEquals(90.0, tv.first().getDouble("position_seconds"), 0.001)
        assertEquals(210.0, tablet.first().getDouble("position_seconds"), 0.001)
    }

    @Test
    fun unresolvedWatchedSourceDoesNotBecomeAnotherLibraryCard() {
        val work = JSONObject().put("history_id", "saved-only-work")
        val unrelatedWatched = JSONObject().put("history_key", "legacy-orphan-episode")
            .put("position_seconds", 12.0)
        val result = historyItemsAfterRead(
            previous = emptyList(),
            savedLibrary = listOf(work),
            watchedHistory = listOf(unrelatedWatched),
            homeEdgeTv = false
        )
        assertEquals(1, result.size)
        assertEquals("saved-only-work", historyKey(result.single()))
    }


    @Test
    fun identicalDownloadedHistoryDoesNotForceRepainting() {
        val first = listOf(
            JSONObject().put("history_id", "one").put("title", "Star Trek").put("episode_key", "s2e1"),
            JSONObject().put("history_id", "two").put("title", "Babylon Berlin")
        )
        val refreshed = listOf(
            JSONObject().put("history_id", "one").put("title", "Star Trek").put("episode_key", "s2e1"),
            JSONObject().put("history_id", "two").put("title", "Babylon Berlin")
        )
        assertTrue(sameMediaHistoryRows(first, refreshed))
        assertTrue(sameMediaHistoryRows(emptyList(), emptyList()))
        refreshed[0].put("episode_key", "s2e2")
        assertTrue(!sameMediaHistoryRows(first, refreshed))
        assertTrue(!sameMediaHistoryRows(first, refreshed.take(1)))
    }
}
