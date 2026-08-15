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
        assertEquals(listOf("https://edge.example"), HomeUpdatePolicy.configuredBaseUrls(" bad ;https://edge.example/;https://edge.example"))
        assertTrue(HomeUpdatePolicy.validApkPath("/download/SkeletonTV.apk"))
        assertFalse(HomeUpdatePolicy.validApkPath("https://other.example/app.apk"))
        assertFalse(HomeUpdatePolicy.validApkPath("/../app.apk"))
    }
}
