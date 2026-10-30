package com.skeleton.home

import java.nio.file.Files
import org.junit.Assert.*
import org.junit.Test

class NativePosterCacheTest {
    @Test fun modeRoundTripAndContentChange() {
        val a=remoteBitmapKey("https://example.test/a.jpg",960,540)
        assertNotEquals(a,remoteBitmapKey("https://example.test/b.jpg",960,540))
        assertEquals(a,remoteBitmapKey("https://example.test/a.jpg",960,540))
        assertNotEquals(a,remoteBitmapKey("https://other.test/a.jpg",960,540))
        assertNotEquals(a,remoteBitmapKey("https://example.test/a.jpg",600,900))
    }
    @Test fun previousOrUnknownPlayerCannotAuthorizePoster() {
        assertEquals("",matchingPoster("b","a","old.jpg"))
        assertEquals("",matchingPoster("b","","old.jpg"))
        assertEquals("",matchingPoster("","a","old.jpg"))
        assertEquals("a.jpg",matchingPoster("a","a","a.jpg"))
        assertEquals("",matchingPoster("a","a",""))
    }
    @Test fun diskEvictsOldestWithinByteAndEntryBounds() {
        val dir=Files.createTempDirectory("synthetic-posters").toFile()
        try {
            val a=java.io.File(dir,"a.img").apply{writeBytes(ByteArray(4));setLastModified(1000)}
            val b=java.io.File(dir,"b.img").apply{writeBytes(ByteArray(4));setLastModified(2000)}
            trimRemoteBitmapDisk(dir,4,2)
            assertFalse(a.exists());assertTrue(b.exists())
            trimRemoteBitmapDisk(dir,100,0)
            assertFalse(b.exists())
            trimRemoteBitmapDisk(dir,4,2) // empty cache/miss is harmless
        } finally {dir.deleteRecursively()}
    }
}
