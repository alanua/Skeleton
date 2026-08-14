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

enum class OperatorDashboardFreshness {
    CURRENT,
    STALE,
    OFFLINE,
    DEGRADED,
}

data class OperatorDashboardSection(
    val titleUk: String,
    val emptyUk: String,
    val rows: List<String>,
)

data class OperatorDashboardState(
    val sourceChannel: String,
    val refreshedAt: String?,
    val freshness: OperatorDashboardFreshness,
    val sections: List<OperatorDashboardSection>,
    val error: String? = null,
)

interface CanonicalHomeApi {
    suspend fun loadPlaceholderState(session: HomeSession): HomePlaceholderState
}

interface OperatorDashboardApi {
    suspend fun loadOperatorDashboard(session: HomeSession): OperatorDashboardState
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
