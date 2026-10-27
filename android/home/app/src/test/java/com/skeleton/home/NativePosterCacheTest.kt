package com.skeleton.home

import org.junit.Assert.*
import org.junit.Test

class NativePosterCacheTest {
    @Test fun requestIdentitySurvivesModeRoundTrip() {
        val a=remoteBitmapKey("https://example.test/a.jpg",960,540)
        val b=remoteBitmapKey("https://example.test/b.jpg",600,900)
        assertNotEquals(a,b)
        assertEquals(a,remoteBitmapKey("https://example.test/a.jpg",960,540))
    }
    @Test fun endpointContentAndDecodeSizeAreIsolated() {
        val a=remoteBitmapKey("https://example.test/a.jpg",960,540)
        assertNotEquals(a,remoteBitmapKey("https://other.test/a.jpg",960,540))
        assertNotEquals(a,remoteBitmapKey("https://example.test/new.jpg",960,540))
        assertNotEquals(a,remoteBitmapKey("https://example.test/a.jpg",720,1080))
    }
}
