package com.skeleton.home

import com.skeleton.home.domain.ConfirmedWebUi
import com.skeleton.home.domain.DeviceInventoryOrdering
import com.skeleton.home.domain.DeviceInventorySnapshot
import com.skeleton.home.domain.DeviceTopologyRole
import com.skeleton.home.domain.HomeDevice
import com.skeleton.home.domain.HomeResidence
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DeviceInventoryTest {
    @Test
    fun groupsExactlyTwoResidencesInConfiguredOrder() {
        val snapshot = DeviceInventorySnapshot(
            residences = listOf(
                HomeResidence(id = "res-b", displayName = "Помешкання B", order = 20),
                HomeResidence(id = "res-a", displayName = "Помешкання A", order = 10),
            ),
            devices = listOf(
                device("b-router", "res-b", "B", DeviceTopologyRole.MAIN_ROUTER, 0),
                device("a-router", "res-a", "A", DeviceTopologyRole.MAIN_ROUTER, 0),
            ),
        )

        val grouped = DeviceInventoryOrdering.group(snapshot)

        assertEquals(listOf("res-a", "res-b"), grouped.map { it.residence.id })
        assertEquals(listOf("a-router"), grouped[0].devices.map { it.id })
        assertEquals(listOf("b-router"), grouped[1].devices.map { it.id })
    }

    @Test
    fun ordersTopologyByTypedRelationsNotBrandOrDisplayName() {
        val devices = listOf(
            device("other", "res", "AAA Router", DeviceTopologyRole.OTHER, 0),
            device("wled-port-8", "res", "ZZZ", DeviceTopologyRole.WLED_CONTROLLER, 0, topologyPort = 8),
            device("downstream", "res", "Not a router", DeviceTopologyRole.DOWNSTREAM_NETWORK, 0),
            device(
                "modem",
                "res",
                "Completely renamed",
                DeviceTopologyRole.ATTACHED_UPLINK,
                0,
                attachedToDeviceId = "main",
            ),
            device("main", "res", "Anything", DeviceTopologyRole.MAIN_ROUTER, 99),
            device("wled-port-2", "res", "BBB", DeviceTopologyRole.WLED_CONTROLLER, 99, topologyPort = 2),
        )
        val snapshot = DeviceInventorySnapshot(
            residences = listOf(HomeResidence("res", "Residence", 0)),
            devices = devices,
        )

        assertEquals(
            listOf("main", "modem", "downstream", "wled-port-2", "wled-port-8", "other"),
            DeviceInventoryOrdering.group(snapshot).single().devices.map { it.id },
        )
    }

    @Test
    fun unattachedUplinkDoesNotPretendToBeMainRouterModem() {
        val snapshot = DeviceInventorySnapshot(
            residences = listOf(HomeResidence("res", "Residence", 0)),
            devices = listOf(
                device("main", "res", "Main", DeviceTopologyRole.MAIN_ROUTER, 0),
                device("downstream", "res", "Downstream", DeviceTopologyRole.DOWNSTREAM_NETWORK, 0),
                device(
                    "orphan-uplink",
                    "res",
                    "Uplink",
                    DeviceTopologyRole.ATTACHED_UPLINK,
                    0,
                    attachedToDeviceId = "unknown",
                ),
            ),
        )

        assertEquals(
            listOf("main", "downstream", "orphan-uplink"),
            DeviceInventoryOrdering.group(snapshot).single().devices.map { it.id },
        )
    }

    @Test
    fun onlyConfirmedSafeWebUiReturnsExactEndpoint() {
        val exact = "http://192.0.2.10/ui"
        val valid = device(
            "valid",
            "res",
            "Valid",
            DeviceTopologyRole.OTHER,
            0,
            webUi = ConfirmedWebUi(exact, confirmed = true),
        )
        val unconfirmed = valid.copy(id = "unconfirmed", webUi = ConfirmedWebUi(exact, confirmed = false))
        val malformed = valid.copy(id = "malformed", webUi = ConfirmedWebUi("not a uri", confirmed = true))
        val credentialed = valid.copy(
            id = "credentialed",
            webUi = ConfirmedWebUi("http://user:pass@192.0.2.10/", confirmed = true),
        )
        val tokenQuery = valid.copy(
            id = "token",
            webUi = ConfirmedWebUi("https://device.invalid/?token=secret", confirmed = true),
        )

        assertEquals(exact, DeviceInventoryOrdering.confirmedWebUi(valid))
        assertNull(DeviceInventoryOrdering.confirmedWebUi(unconfirmed))
        assertNull(DeviceInventoryOrdering.confirmedWebUi(malformed))
        assertNull(DeviceInventoryOrdering.confirmedWebUi(credentialed))
        assertNull(DeviceInventoryOrdering.confirmedWebUi(tokenQuery))
    }

    private fun device(
        id: String,
        residenceId: String,
        displayName: String,
        role: DeviceTopologyRole,
        registryOrder: Int,
        attachedToDeviceId: String? = null,
        topologyPort: Int? = null,
        webUi: ConfirmedWebUi? = null,
    ) = HomeDevice(
        id = id,
        residenceId = residenceId,
        displayName = displayName,
        role = role,
        registryOrder = registryOrder,
        attachedToDeviceId = attachedToDeviceId,
        topologyPort = topologyPort,
        webUi = webUi,
    )
}
