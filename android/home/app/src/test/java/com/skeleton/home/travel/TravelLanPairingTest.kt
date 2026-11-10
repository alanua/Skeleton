package com.skeleton.home.travel

import java.net.InetAddress
import org.junit.Assert.*
import org.junit.Test

class TravelLanPairingTest {
    private fun rejects(block: () -> Unit) {
        try { block(); fail("expected rejection") } catch (_: IllegalArgumentException) {}
    }
    @Test fun randomInvitesRoundTripAndRedactSecrets() {
        val address = InetAddress.getByName("127.0.0.1")
        TravelLanPairing.create(address, 12345).use { first ->
            TravelLanPairing.create(address, 12345).use { second ->
                assertNotEquals(first.qrPayload(), second.qrPayload())
                TravelLanPairing.parse(first.qrPayload()).use { parsed ->
                    assertEquals(first.qrPayload(), parsed.qrPayload())
                    assertEquals(32, parsed.key().size)
                    assertFalse(parsed.toString().contains(parsed.linkId))
                }
            }
        }
    }
    @Test fun malformedPublicDnsAndVersionInvitesAreRejected() {
        TravelLanPairing.create(InetAddress.getByName("127.0.0.1"), 12345).use { invite ->
            val qr = invite.qrPayload()
            for (bad in listOf(qr.replace("127.0.0.1", "8.8.8.8"),
                qr.replace("127.0.0.1", "example.org"), qr.replace("127.0.0.1", "0.0.0.0"),
                qr.replace("127.0.0.1", "127.00.0.1"), qr.replace(":12345:", ":0:"),
                qr.replace("s20lan:1:", "s20lan:2:"), qr + ":extra", "x".repeat(181))) {
                rejects { TravelLanPairing.parse(bad) }
            }
            invite.close()
            try { invite.qrPayload(); fail("closed invite") } catch (_: IllegalStateException) {}
        }
    }
    @Test fun onlyPrivateIpv4AndLoopbackAreAllowed() {
        for (ip in listOf("10.1.2.3", "172.16.0.1", "172.31.255.254", "192.168.1.1", "127.0.0.1"))
            assertTrue(TravelLanPairing.allowed(InetAddress.getByName(ip)))
        for (ip in listOf("172.32.0.1", "169.254.1.1", "0.0.0.0", "224.0.0.1", "::1"))
            assertFalse(TravelLanPairing.allowed(InetAddress.getByName(ip)))
    }
}
