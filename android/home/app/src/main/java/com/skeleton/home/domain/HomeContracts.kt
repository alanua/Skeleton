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
