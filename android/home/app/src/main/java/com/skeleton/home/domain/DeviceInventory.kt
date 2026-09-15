package com.skeleton.home.domain

import java.net.URI

enum class DeviceTopologyRole {
    MAIN_ROUTER,
    ATTACHED_UPLINK,
    DOWNSTREAM_NETWORK,
    WLED_CONTROLLER,
    OTHER,
}

data class ConfirmedWebUi(
    val uri: String,
    val confirmed: Boolean,
)

data class HomeDevice(
    val id: String,
    val residenceId: String,
    val displayName: String,
    val role: DeviceTopologyRole,
    val registryOrder: Int,
    val attachedToDeviceId: String? = null,
    val topologyPort: Int? = null,
    val webUi: ConfirmedWebUi? = null,
)

data class HomeResidence(
    val id: String,
    val displayName: String,
    val order: Int,
)

data class DeviceInventorySnapshot(
    val residences: List<HomeResidence>,
    val devices: List<HomeDevice>,
)

data class ResidenceDevices(
    val residence: HomeResidence,
    val devices: List<HomeDevice>,
)

interface DeviceInventoryRepository {
    fun loadDeviceInventory(): DeviceInventorySnapshot
}

object DeviceInventoryOrdering {
    fun group(snapshot: DeviceInventorySnapshot): List<ResidenceDevices> =
        snapshot.residences
            .sortedWith(compareBy<HomeResidence> { it.order }.thenBy { it.id })
            .map { residence ->
                val residenceDevices = snapshot.devices.filter { it.residenceId == residence.id }
                ResidenceDevices(
                    residence = residence,
                    devices = residenceDevices.sortedWith(deviceComparator(residenceDevices)),
                )
            }

    fun confirmedWebUi(device: HomeDevice): String? {
        val endpoint = device.webUi ?: return null
        if (!endpoint.confirmed) return null
        return endpoint.uri.takeIf(::isConfirmedWebUiUri)
    }

    private fun deviceComparator(devices: List<HomeDevice>): Comparator<HomeDevice> {
        val mainRouterIds = devices
            .filter { it.role == DeviceTopologyRole.MAIN_ROUTER }
            .mapTo(mutableSetOf()) { it.id }

        return compareBy<HomeDevice> { topologyRank(it, mainRouterIds) }
            .thenBy { if (it.role == DeviceTopologyRole.WLED_CONTROLLER) it.topologyPort ?: Int.MAX_VALUE else Int.MAX_VALUE }
            .thenBy { it.registryOrder }
            .thenBy { it.id }
    }

    private fun topologyRank(device: HomeDevice, mainRouterIds: Set<String>): Int = when (device.role) {
        DeviceTopologyRole.MAIN_ROUTER -> 0
        DeviceTopologyRole.ATTACHED_UPLINK -> if (device.attachedToDeviceId in mainRouterIds) 1 else 4
        DeviceTopologyRole.DOWNSTREAM_NETWORK -> 2
        DeviceTopologyRole.WLED_CONTROLLER -> 3
        DeviceTopologyRole.OTHER -> 4
    }

    private fun isConfirmedWebUiUri(raw: String): Boolean {
        val parsed = runCatching { URI(raw) }.getOrNull() ?: return false
        val scheme = parsed.scheme?.lowercase() ?: return false
        if (scheme != "http" && scheme != "https") return false
        if (parsed.host.isNullOrBlank()) return false
        if (!parsed.userInfo.isNullOrBlank()) return false

        val sensitiveQuery = parsed.rawQuery.orEmpty().lowercase()
        if (
            sensitiveQuery.contains("token=") ||
            sensitiveQuery.contains("secret=") ||
            sensitiveQuery.contains("password=") ||
            sensitiveQuery.contains("api_key=") ||
            sensitiveQuery.contains("apikey=")
        ) {
            return false
        }
        return true
    }
}
