package com.skeleton.home.data

import com.skeleton.home.domain.CanonicalHomeApi
import com.skeleton.home.domain.ConnectivityMonitor
import com.skeleton.home.domain.ConnectivityStatus
import com.skeleton.home.domain.DeviceInventoryRepository
import com.skeleton.home.domain.DeviceInventorySnapshot
import com.skeleton.home.domain.HomePlaceholderState
import com.skeleton.home.domain.HomeSession
import com.skeleton.home.domain.VerifiedActionState
import com.skeleton.home.domain.VerifiedActionStateStore

class SyntheticHomeRepository(
    private val connectivityMonitor: ConnectivityMonitor = StaticConnectivityMonitor(),
    private val actionStateStore: VerifiedActionStateStore = InMemoryVerifiedActionStateStore(),
    private val deviceInventory: DeviceInventorySnapshot = DeviceInventorySnapshot(
        residences = emptyList(),
        devices = emptyList(),
    ),
) : CanonicalHomeApi, DeviceInventoryRepository {
    override suspend fun loadPlaceholderState(session: HomeSession): HomePlaceholderState =
        HomePlaceholderState(
            connectivityStatus = connectivityMonitor.currentStatus(),
            actionState = actionStateStore.currentActionState(),
            summary = "Синтетичний режим: канонічне джерело Home ще не підключене.",
        )

    override fun loadDeviceInventory(): DeviceInventorySnapshot = deviceInventory
}

class StaticConnectivityMonitor(
    private val status: ConnectivityStatus = ConnectivityStatus.OFFLINE,
) : ConnectivityMonitor {
    override fun currentStatus(): ConnectivityStatus = status
}

class InMemoryVerifiedActionStateStore : VerifiedActionStateStore {
    private var state: VerifiedActionState? = null

    override fun currentActionState(): VerifiedActionState? = state

    override fun recordActionState(state: VerifiedActionState) {
        this.state = state
    }
}
