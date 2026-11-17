package com.skeleton.home

internal data class HomeMonitorIdentity(val server: String?, val endpoint: String, val job: String)
internal data class HomeMonitorToken(val generation: Long, val identity: HomeMonitorIdentity)

/** One gate for the screen lifetime; every acceptance also checks the CURRENT selection. */
internal class HomeMonitorRequests {
    private var generation = 0L
    private var writing = false
    fun invalidate() { generation++; writing = false }
    fun read(identity: HomeMonitorIdentity): HomeMonitorToken? =
        if (writing) null else HomeMonitorToken(++generation, identity)
    fun write(identity: HomeMonitorIdentity): HomeMonitorToken {
        writing = true
        return HomeMonitorToken(++generation, identity)
    }
    fun accepts(token: HomeMonitorToken, current: HomeMonitorIdentity): Boolean =
        token.generation == generation && token.identity == current
    fun finish(token: HomeMonitorToken, current: HomeMonitorIdentity) {
        if (accepts(token, current)) writing = false
    }
}

internal fun monitorEligible(hasJob: Boolean, isSeries: Boolean, supported: Boolean?, tmdbId: Int): Boolean =
    hasJob && isSeries && (supported ?: (tmdbId > 0))

internal fun monitorFailure(status: Int?, timeout: Boolean): String = when {
    status == 409 -> "UNSUPPORTED"
    status == 504 || timeout -> "TIMEOUT"
    else -> "ERROR"
}
