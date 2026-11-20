package com.skeleton.home

import org.junit.Assert.*
import org.junit.Test

class HomeMovieAtlasTest {
    private val context = MovieAtlasContext("https://home.example:8443", "samsung &/✓?#+", true)
    private val capabilities = mapOf(
        "schema" to "skeleton.media.movie_atlas.capabilities.v1",
        "protocol" to 1,
        "supported" to true,
        "controller_path" to "/movie-atlas",
        "supported_targets" to listOf("tv", context.endpoint),
        "presentation_ready" to true
    )

    @Test fun buildsOnlyTheCurrentOriginAndEncodesTheSelectedEndpoint() {
        val expected = "https://home.example:8443/movie-atlas?endpoint_id=samsung%20%26%2F%E2%9C%93%3F%23%2B"
        assertEquals(expected, movieAtlasControllerUrl(context, capabilities))
        assertEquals(expected, movieAtlasControllerUrl(context.copy(server = context.server + "/"), capabilities))
        assertEquals(expected, movieAtlasControllerUrl(context, capabilities + ("protocol" to 1L)))
        assertEquals(
            "http://[2001:db8::1]:8080/movie-atlas?endpoint_id=tv",
            movieAtlasControllerUrl(context.copy(server = "http://[2001:db8::1]:8080", endpoint = "tv"), capabilities)
        )
    }

    @Test fun requiresExactProtocolAndReadinessFields() {
        for ((field, value) in listOf(
            "schema" to "skeleton.media.movie_atlas.capabilities.v2",
            "protocol" to 2, "protocol" to "1", "protocol" to 1.0,
            "supported" to false, "supported" to "true", "presentation_ready" to false,
            "presentation_ready" to "true", "supported_targets" to "tv",
            "supported_targets" to listOf(context.endpoint, 10),
            "supported_targets" to listOf(context.endpoint, " ")
        )) {
            assertNull(field, movieAtlasControllerUrl(context, capabilities + (field to value)))
        }
        for (field in capabilities.keys) assertNull(field, movieAtlasControllerUrl(context, capabilities - field))
    }

    @Test fun doesNotSubstituteAnotherSupportedEndpoint() {
        assertNull(movieAtlasControllerUrl(context.copy(endpoint = "another"), capabilities))
        assertNull(movieAtlasControllerUrl(context.copy(endpoint = null), capabilities))
        assertNull(movieAtlasControllerUrl(context, capabilities + ("supported_targets" to listOf("tv"))))
        for (invalid in listOf("", "  ", "tv\n", "\uD800")) {
            val invalidContext = context.copy(endpoint = invalid)
            assertNull(movieAtlasServer(invalidContext))
            assertNull(movieAtlasControllerUrl(invalidContext, capabilities + ("supported_targets" to listOf(invalid))))
        }
    }

    @Test fun rejectsMalformedServersAndAlternateControllerPaths() {
        for (invalid in listOf(
            "//home.example", "https://user@home.example", "https://home.example@outside.example",
            "https://home.example:", "https://home.example:0", "https://home.example:65536",
            "https://home.example/path", "https://home.example/?", "https://home.example/#",
            "https://home.example\\evil", "https://%68ome.example", "file:///tmp/atlas",
            "javascript:alert(1)", "data:text/html,atlas"
        )) {
            assertNull(invalid, movieAtlasServer(context.copy(server = invalid)))
            assertNull(invalid, movieAtlasControllerUrl(context.copy(server = invalid), capabilities))
        }
        for (invalid in listOf(
            "https://outside.example/movie-atlas", "//home.example/movie-atlas",
            "/movie-atlas?endpoint_id=tv", "/movie-atlas#x", "/movie-atlas/../movie-atlas",
            "/%6dovie-atlas", "javascript:alert(1)", "data:text/html,atlas"
        )) {
            assertNull(invalid, movieAtlasControllerUrl(context, capabilities + ("controller_path" to invalid)))
        }
    }

    @Test fun launchGenerationIsBoundToTheActiveServerAndEndpoint() {
        val first = MovieAtlasRequest(context)
        val second = MovieAtlasRequest(context)
        assertTrue(first.accepts(first, context))
        assertFalse(first.accepts(second, context))
        assertFalse(first.accepts(null, context))
        assertFalse(first.accepts(first, context.copy(server = "https://elsewhere.example")))
        assertFalse(first.accepts(first, context.copy(endpoint = "tv")))
        assertFalse(first.accepts(first, context.copy(active = false)))
        assertNull(movieAtlasControllerUrl(context.copy(active = false), capabilities))
        assertTrue(second.accepts(second, context))
    }

    @Test fun allowsControllerAndInterceptsOnlyTheSameOriginExit() {
        val url = requireNotNull(movieAtlasControllerUrl(context, capabilities))
        assertEquals(MovieAtlasNavigation.ALLOW, movieAtlasNavigation(url, url))
        assertEquals(MovieAtlasNavigation.ALLOW, movieAtlasNavigation(url, "$url#search"))
        assertEquals(
            MovieAtlasNavigation.EXIT,
            movieAtlasNavigation(url, "https://home.example:8443/movie-atlas/exit")
        )
        for (blocked in listOf(
            "https://outside.example:8443/movie-atlas/exit", "https://home.example/movie-atlas/exit",
            "http://home.example:8443/movie-atlas/exit", "https://user@home.example:8443/movie-atlas/exit",
            "https://home.example:8443/movie-atlas?endpoint_id=tv",
            "$url&extra=1", "https://home.example:8443/other", "//home.example:8443/movie-atlas/exit",
            "javascript:alert(1)", "file:///tmp/atlas", "data:text/html,atlas"
        )) {
            assertEquals(blocked, MovieAtlasNavigation.BLOCK, movieAtlasNavigation(url, blocked))
        }
    }
}
