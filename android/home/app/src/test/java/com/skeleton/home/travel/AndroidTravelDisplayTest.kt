package com.skeleton.home.travel

import androidx.activity.ComponentActivity
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowBuild

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class AndroidTravelDisplayTest {
    private val remote = object : TravelRemoteSessionPort {
        override fun publish(state: TravelSessionState) {}
    }

    @Test fun onlyBaseSamsungS20PhonesPassGate() {
        for (model in listOf("SM-G980F", "SM-G980F/DS", "SM-G981B", "SM-G981U1", "sm-g981w")) {
            assertTrue(model, supportsTravelDisplay("samsung", model, false))
            assertFalse(model, supportsTravelDisplay("samsung", model, true))
        }
        for (model in listOf("SM-G985F", "SM-G986B", "SM-G988B", "SM-G780F", "SM-G781B",
            "SM-X800", "SM-G981", "SM-G981B-extra", "S20")) {
            assertFalse(model, supportsTravelDisplay("samsung", model, false))
        }
        assertFalse(supportsTravelDisplay("other", "SM-G981B", false))
    }

    @Test fun unsupportedHostDoesNotOpenTravelSession() {
        ShadowBuild.setManufacturer("other")
        ShadowBuild.setModel("SM-G981B")
        val host = Robolectric.buildActivity(ComponentActivity::class.java).setup()
        assertNull(AndroidTravelDisplay.open(host.get(), "synthetic", remote))
        host.pause().stop().destroy()
    }

    @Test fun noDisplayRejectsOutputAndCloseIsTerminalAndIdempotent() {
        ShadowBuild.setManufacturer("samsung")
        ShadowBuild.setModel("SM-G981B")
        val host = Robolectric.buildActivity(ComponentActivity::class.java).setup()
        val owner = requireNotNull(AndroidTravelDisplay.open(host.get(), "synthetic", remote))
        assertEquals("hdmi_unavailable", owner.session.selectOutput(TravelOutput.HDMI).reason)
        owner.session.allowRemoteControl(true)
        owner.close()
        owner.close()
        owner.onDisplayAdded(999)
        assertFalse(owner.session.snapshot().hdmiAvailable)
        assertEquals("closed", owner.session.execute(
            TravelMediaCommand("synthetic", owner.session.snapshot().revision, TravelAction.PLAY,
                TravelOrigin.REMOTE)).reason)
        host.pause().stop().destroy()
    }
    @Test fun hostStopAutomaticallyClosesSession() {
        ShadowBuild.setManufacturer("samsung")
        ShadowBuild.setModel("SM-G980F")
        val host = Robolectric.buildActivity(ComponentActivity::class.java).setup()
        val owner = requireNotNull(AndroidTravelDisplay.open(host.get(), "synthetic", remote))
        host.pause().stop()
        assertEquals("closed", owner.session.selectOutput(TravelOutput.HDMI).reason)
        host.destroy()
    }

}
