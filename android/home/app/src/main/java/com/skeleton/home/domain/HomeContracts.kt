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
    val surface: HomeControlSurfaceState,
)

enum class HomeMode {
    YOUTUBE,
    CAST,
    TV,
    GAMES,
}

enum class HomeControlCapability {
    SEEK_BACK_15,
    PLAY_PAUSE,
    SEEK_FORWARD_15,
    MUTE,
    VOLUME,
}

data class HomeModeContext(
    val mode: HomeMode,
    val label: String,
    val isActive: Boolean,
    val isAvailable: Boolean,
)

data class SyntheticActiveMedia(
    val title: String,
    val year: String,
    val seasonEpisodeLine: String?,
    val artworkLabel: String,
)

data class SyntheticPlaybackState(
    val positionLabel: String,
    val durationLabel: String,
    val progress: Float,
    val isPlaying: Boolean,
    val isMuted: Boolean,
    val volume: Float,
)

data class HomeControlSurfaceState(
    val modes: List<HomeModeContext>,
    val activeMedia: SyntheticActiveMedia,
    val playback: SyntheticPlaybackState,
    val capabilities: Set<HomeControlCapability>,
)

interface CanonicalHomeApi {
    suspend fun loadPlaceholderState(session: HomeSession): HomePlaceholderState
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
