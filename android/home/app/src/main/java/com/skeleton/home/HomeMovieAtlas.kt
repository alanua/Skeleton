package com.skeleton.home

import java.net.URI
import java.net.URISyntaxException
import java.net.URLEncoder
import java.util.Locale

internal const val MOVIE_ATLAS_CAPABILITIES = "/api/movie-atlas/capabilities"
internal const val MOVIE_ATLAS_SCHEMA = "skeleton.media.movie_atlas.capabilities.v1"
private const val CONTROLLER = "/movie-atlas"

internal data class MovieAtlasContext(val server: String?, val endpoint: String?, val active: Boolean)

// Each instance identifies one launch attempt, even when its context repeats.
internal class MovieAtlasRequest(val context: MovieAtlasContext) {
    fun accepts(current: MovieAtlasRequest?, owner: MovieAtlasContext): Boolean =
        this === current && owner.active && owner == context
}

private fun atlasUri(value: String): URI? {
    val uri = try {
        URI(value)
    } catch (_: URISyntaxException) {
        return null
    }
    return uri.takeIf {
        it.scheme?.lowercase(Locale.ROOT) in setOf("http", "https") &&
            it.host != null && it.rawUserInfo == null &&
            !it.rawAuthority.endsWith(":") &&
            (it.port == -1 || it.port in 1..65535)
    }
}

private fun validAtlasTarget(value: String): Boolean =
    value.isNotBlank() && value.none { it.isISOControl() } &&
        Charsets.UTF_8.newEncoder().canEncode(value)

internal fun movieAtlasServer(context: MovieAtlasContext): URI? {
    if (!context.active || context.endpoint?.let(::validAtlasTarget) != true) return null
    return context.server?.let(::atlasUri)?.takeIf {
        (it.rawPath.isEmpty() || it.rawPath == "/") && it.rawQuery == null && it.rawFragment == null
    }
}

internal fun movieAtlasControllerUrl(context: MovieAtlasContext, capabilities: Map<String, Any?>): String? {
    val server = movieAtlasServer(context) ?: return null
    val endpoint = context.endpoint ?: return null
    val protocol = capabilities["protocol"]
    val targets = capabilities["supported_targets"] as? List<*> ?: return null
    if (capabilities["schema"] != MOVIE_ATLAS_SCHEMA ||
        (protocol != 1 && protocol != 1L) ||
        capabilities["supported"] != true || capabilities["presentation_ready"] != true ||
        capabilities["controller_path"] != CONTROLLER ||
        targets.any { it !is String || !validAtlasTarget(it) } || endpoint !in targets
    ) return null

    val encodedEndpoint = URLEncoder.encode(endpoint, "UTF-8").replace("+", "%20")
    return "${server.scheme.lowercase(Locale.ROOT)}://${server.rawAuthority}$CONTROLLER?endpoint_id=$encodedEndpoint"
}

internal enum class MovieAtlasNavigation { ALLOW, EXIT, BLOCK }

private fun URI.originPort(): Int = when {
    port != -1 -> port
    scheme.equals("https", ignoreCase = true) -> 443
    else -> 80
}

internal fun movieAtlasNavigation(controller: String, destination: String): MovieAtlasNavigation {
    val expected = atlasUri(controller) ?: return MovieAtlasNavigation.BLOCK
    val actual = atlasUri(destination) ?: return MovieAtlasNavigation.BLOCK
    if (!expected.scheme.equals(actual.scheme, ignoreCase = true) ||
        !expected.host.equals(actual.host, ignoreCase = true) || expected.originPort() != actual.originPort()
    ) return MovieAtlasNavigation.BLOCK

    return when {
        actual.rawPath == "$CONTROLLER/exit" && actual.rawQuery == null &&
            actual.rawFragment == null -> MovieAtlasNavigation.EXIT
        actual.rawPath == CONTROLLER && actual.rawQuery == expected.rawQuery -> MovieAtlasNavigation.ALLOW
        else -> MovieAtlasNavigation.BLOCK
    }
}
