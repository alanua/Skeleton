package com.skeleton.home

import com.skeleton.home.homeedge.GmailOAuthHomeEdgeApi
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class GmailOAuthHomeEdgeApiTest {
    @Test
    fun startReturnsGoogleAuthorizationUrlWithoutCredentialsInClient() {
        val api = GmailOAuthHomeEdgeApi("http://home-edge.fixture") { baseUrl, path, payload ->
            assertEquals("http://home-edge.fixture", baseUrl)
            assertEquals("/api/mobile/gmail/oauth/start", path)
            assertEquals(null, payload)
            """{"authorization_url":"https://accounts.google.com/o/oauth2/v2/auth?state=fixture-state","message":"Відкрийте Google"}"""
        }

        val result = api.start()

        assertTrue(result.authorizationUrl.startsWith("https://accounts.google.com/"))
        assertEquals("Відкрийте Google", result.message)
    }

    @Test
    fun completePostsReturnedUrlToExistingHomeEdgeEndpoint() {
        val api = GmailOAuthHomeEdgeApi("http://home-edge.fixture") { _, path, payload ->
            assertEquals("/api/mobile/gmail/oauth/callback-complete", path)
            assertTrue(payload?.contains("returned_url") == true)
            assertTrue(payload?.contains("fixture-code") == true)
            """{"ok":true,"message":"Gmail авторизовано"}"""
        }

        val result = api.complete("http://localhost/?code=fixture-code&state=fixture-state")

        assertEquals("Gmail авторизовано", result.message)
    }

    @Test
    fun completeFailsClosedOnRejectedAuthorization() {
        val api = GmailOAuthHomeEdgeApi("http://home-edge.fixture") { _, _, _ ->
            error("OAuth state mismatch")
        }

        val failure = runCatching {
            api.complete("http://localhost/?code=fixture-code&state=wrong-fixture-state")
        }.exceptionOrNull()

        assertTrue(failure?.message?.contains("OAuth state mismatch") == true)
    }

    @Test
    fun completeFailsClosedWhenSuccessMarkerIsMissing() {
        val api = GmailOAuthHomeEdgeApi("http://home-edge.fixture") { _, _, _ ->
            """{"message":"callback accepted"}"""
        }

        val failure = runCatching {
            api.complete("http://localhost/?code=fixture-code&state=fixture-state")
        }.exceptionOrNull()

        assertTrue(failure?.message?.contains("callback accepted") == true)
    }

    @Test
    fun completeRejectsBlankReturnedUrlWithoutNetworkCall() {
        var called = false
        val api = GmailOAuthHomeEdgeApi("http://home-edge.fixture") { _, _, _ ->
            called = true
            "{}"
        }

        val failure = runCatching { api.complete("   ") }.exceptionOrNull()

        assertTrue(failure?.message?.contains("Вставте адресу після Google") == true)
        assertTrue(!called)
    }
}
