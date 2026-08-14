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

data class OperatorLiveItem(
    val title: String,
    val detail: String,
    val updatedAt: Long,
    val drillDown: Map<String, String> = emptyMap(),
)

data class OperatorDashboardSections(
    val workingNow: List<OperatorLiveItem> = emptyList(),
    val waiting: List<OperatorLiveItem> = emptyList(),
    val needsAttention: List<OperatorLiveItem> = emptyList(),
    val recentlyDone: List<OperatorLiveItem> = emptyList(),
    val next: List<OperatorLiveItem> = emptyList(),
)

data class OperatorDashboardState(
    val connectivityStatus: ConnectivityStatus,
    val stale: Boolean,
    val refreshedAt: Long?,
    val message: String,
    val sections: OperatorDashboardSections = OperatorDashboardSections(),
)

interface CanonicalHomeApi {
    suspend fun loadPlaceholderState(session: HomeSession): HomePlaceholderState
}

interface OperatorDashboardRepository {
    suspend fun loadDashboard(session: HomeSession): OperatorDashboardState
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
