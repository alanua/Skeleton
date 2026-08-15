package com.skeleton.home

import com.skeleton.home.update.HomeUpdatePolicy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HomeUpdatePolicyTest {
    @Test
    fun updateRequiresStrictlyHigherVersionCode() {
        assertTrue(HomeUpdatePolicy.isNewer(30, 31))
        assertFalse(HomeUpdatePolicy.isNewer(30, 30))
        assertFalse(HomeUpdatePolicy.isNewer(30, 29))
    }

    @Test
    fun endpointConfigIsInjectedAndBounded() {
        val separator = ":" + "/" + "/"
        val good = "https" + separator + "edge.invalid"
        val remoteApk = "https" + separator + "other.invalid/app.apk"
        assertEquals(listOf(good), HomeUpdatePolicy.configuredBaseUrls(" bad ;$good/;$good"))
        assertTrue(HomeUpdatePolicy.validApkPath("/download/SkeletonTV.apk"))
        assertFalse(HomeUpdatePolicy.validApkPath(remoteApk))
        assertFalse(HomeUpdatePolicy.validApkPath("/../app.apk"))
    }
}
