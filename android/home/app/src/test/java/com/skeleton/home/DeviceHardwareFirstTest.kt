package com.skeleton.home

import org.junit.Assert.assertTrue
import org.junit.Test

class DeviceHardwareFirstTest {
    @Test fun physicalEquipmentIsListedBeforeSoftware() {
        assertTrue(hardwareFirstRank("usb") < hardwareFirstRank("software"))
        assertTrue(hardwareFirstRank("hdmi") < hardwareFirstRank("container"))
        assertTrue(hardwareFirstRank("ethernet") < hardwareFirstRank("virtual_machine"))
    }
}
