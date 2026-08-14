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

enum class OperatorLiveStateStatus {
    CURRENT,
    STALE,
    OFFLINE,
}

data class OperatorDashboardSection(
    val title: String,
    val value: String,
    val status: String,
    val detail: String? = null,
)

data class OperatorLiveState(
    val status: OperatorLiveStateStatus,
    val observedAtEpochSeconds: Long?,
    val checkedAtEpochSeconds: Long,
    val staleAfterSeconds: Long,
    val sections: List<OperatorDashboardSection>,
    val offlineReason: String?,
) {
    companion object {
        fun offline(
            reason: String,
            checkedAtEpochSeconds: Long,
        ): OperatorLiveState =
            OperatorLiveState(
                status = OperatorLiveStateStatus.OFFLINE,
                observedAtEpochSeconds = null,
                checkedAtEpochSeconds = checkedAtEpochSeconds,
                staleAfterSeconds = 60,
                sections = emptyList(),
                offlineReason = reason,
            )
    }
}

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
