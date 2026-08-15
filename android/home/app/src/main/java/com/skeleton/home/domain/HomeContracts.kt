package com.skeleton.home.domain

enum class UserRole {
    OPERATOR,
    ORDINARY,
    SPOUSE,
}

enum class ConnectivityStatus {
    ONLINE,
    DEGRADED,
    OFFLINE,
}

enum class VerifiedActionState {
    SENT,
    ACCEPTED,
    APPLIED,
    PHYSICALLY_VERIFIED,
}

data class HomeSession(
    val syntheticUserId: String,
    val role: UserRole,
)

data class HomePlaceholderState(
    val connectivityStatus: ConnectivityStatus,
    val actionState: VerifiedActionState?,
    val summary: String,
)

interface CanonicalHomeApi {
    suspend fun loadPlaceholderState(session: HomeSession): HomePlaceholderState
}

enum class MediaSourceSearchStatus {
    IDLE,
    SEARCHING,
    READY,
    EMPTY,
    SOURCES_UNAVAILABLE,
}

data class MediaSourceOption(
    val sourceId: String,
    val title: String,
    val quality: String?,
    val translation: String?,
    val audioTracks: List<String> = emptyList(),
    val subtitles: List<String> = emptyList(),
    val season: Int? = null,
    val episode: Int? = null,
    val playable: Boolean = true,
)

data class MediaSourceFacets(
    val seasons: List<Int> = emptyList(),
    val episodes: List<Int> = emptyList(),
    val qualities: List<String> = emptyList(),
    val translations: List<String> = emptyList(),
    val audioTracks: List<String> = emptyList(),
    val subtitles: List<String> = emptyList(),
)

data class MediaSourceSearchUiState(
    val status: MediaSourceSearchStatus,
    val sources: List<MediaSourceOption> = emptyList(),
    val facets: MediaSourceFacets = MediaSourceFacets(),
    val retryAttempt: Long = 0,
) {
    val message: String?
        get() = when (status) {
            MediaSourceSearchStatus.EMPTY -> "Реліз не знайдено"
            MediaSourceSearchStatus.SOURCES_UNAVAILABLE -> "Джерела не відповіли"
            else -> null
        }

    fun retry(): MediaSourceSearchUiState = copy(
        status = MediaSourceSearchStatus.SEARCHING,
        retryAttempt = retryAttempt + 1,
    )

    companion object {
        fun fromFailure(rawError: String?): MediaSourceSearchUiState {
            val normalized = rawError.orEmpty().lowercase()
            val status = if (
                normalized.contains("timeout") ||
                normalized.contains("timed out") ||
                normalized.contains("failed to connect")
            ) {
                MediaSourceSearchStatus.SOURCES_UNAVAILABLE
            } else {
                MediaSourceSearchStatus.EMPTY
            }
            return MediaSourceSearchUiState(status = status)
        }
    }
}

interface AuthSessionProvider {
    fun currentSession(): HomeSession
    fun canAccessOperatorHub(session: HomeSession): Boolean
}

interface ConnectivityMonitor {
    fun currentStatus(): ConnectivityStatus
}

interface SecureStorage {
    suspend fun putOpaqueValue(key: String, value: ByteArray)
    suspend fun readOpaqueValue(key: String): ByteArray?
    suspend fun remove(key: String)
}

interface VerifiedActionStateStore {
    fun currentActionState(): VerifiedActionState?
    fun recordActionState(state: VerifiedActionState)
}
