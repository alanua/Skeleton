package com.skeleton.home.travel

import java.io.DataOutputStream
import java.net.InetAddress
import java.net.Socket
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.junit.Assert.*
import org.junit.Test

class TravelLanTransportTest {
    private val loopback = InetAddress.getByName("127.0.0.1")
    private class Player : TravelLocalPlayer {
        var stops = 0
        override fun load(media: TravelMedia, output: TravelOutput) {}
        override fun play() {}
        override fun pause() {}
        override fun seekTo(positionMs: Long) {}
        override fun stop() { stops++ }
    }
    private fun session(player: Player) = TravelMediaSession("synthetic", player,
        object : TravelRemoteSessionPort { override fun publish(state: TravelSessionState) {} })
    private fun disabled(session: TravelMediaSession) {
        assertEquals("remote_disabled", session.execute(TravelMediaCommand("synthetic",
            session.snapshot().revision, TravelAction.STOP, TravelOrigin.REMOTE)).reason)
    }
    @Test fun loopbackRoundTripDisconnectRevokesAndInviteIsSingleUse() {
        val player = Player()
        val session = session(player)
        val worker = Executors.newSingleThreadExecutor()
        val host = TravelLanTransport.host(true, loopback, 2000)
        val invite = TravelLanPairing.parse(host.invite.qrPayload())
        val serving = worker.submit { runCatching { host.serve(session) { it() } } }
        try {
            TravelLanTransport.connect(true, invite, "synthetic", 2000).use { client ->
                assertTrue(client.execute(TravelMediaCommand("synthetic", 0, TravelAction.STOP)).accepted)
                assertEquals("stale_revision", client.execute(TravelMediaCommand("synthetic", 0, TravelAction.STOP)).reason)
                assertEquals(1, player.stops)
            }
            serving.get(3, TimeUnit.SECONDS)
            disabled(session)
            assertEquals(1, player.stops) // Revocation leaves local playback alone.
            try { host.serve(session) { it() }; fail("single use") } catch (_: IllegalStateException) {}
        } finally { host.close(); worker.shutdownNow() }
    }
    @Test fun malformedLengthsTruncationTamperAndReplayFailBeforePlayerEffects() {
        for (kind in listOf("negative", "oversize", "truncated", "tamper", "wrongKey", "reflection", "replay")) {
            val worker = Executors.newSingleThreadExecutor()
            val host = TravelLanTransport.host(true, loopback, 500)
            val player = Player()
            val session = session(player)
            val invite = TravelLanPairing.parse(host.invite.qrPayload())
            val serving = worker.submit { runCatching { host.serve(session) { it() } } }
            try {
                Socket(loopback, invite.port).use { socket ->
                    socket.soTimeout = 2000
                    val out = DataOutputStream(socket.getOutputStream())
                    when (kind) {
                        "negative" -> out.writeInt(-1)
                        "oversize" -> out.writeInt(65537)
                        "truncated" -> { out.writeInt(100); out.writeByte(0); socket.shutdownOutput() }
                        else -> {
                            val key = if (kind == "wrongKey") ByteArray(32) else invite.key()
                            TravelSecureChannel(key, invite.linkId, kind == "reflection").use { channel ->
                                val hello = channel.seal("s20-lan-v1-ready".toByteArray())
                                if (kind == "tamper") hello[hello.lastIndex] = (hello.last().toInt() xor 1).toByte()
                                out.writeInt(hello.size); out.write(hello); out.flush()
                                if (kind == "replay") {
                                    val input = java.io.DataInputStream(socket.getInputStream())
                                    val size = input.readInt()
                                    assertTrue(size in 28..65536)
                                    channel.open(ByteArray(size).also { input.readFully(it) })
                                    out.writeInt(hello.size); out.write(hello); out.flush()
                                }
                            }
                            key.fill(0)
                        }
                    }
                    out.flush()
                    try { assertEquals(-1, socket.getInputStream().read()) } catch (_: java.net.SocketException) {}
                }
                serving.get(3, TimeUnit.SECONDS)
                assertEquals(0, player.stops)
                disabled(session)
                val ungranted = TravelSecureChannel(ByteArray(32) { it.toByte() },
                    "after-pre-auth-failure-$kind", true)
                try { TravelSecureHost(session, ungranted); fail("grant leaked") }
                catch (_: IllegalStateException) {}
                try { ungranted.seal(byteArrayOf()); fail("channel open") }
                catch (_: IllegalStateException) {}
            } finally { invite.close(); host.close(); worker.shutdownNow() }
        }
    }
    @Test fun stalledPeerAndOwnerCloseUnblockAndRevoke() {
        for (ownerClose in listOf(false, true)) {
            val worker = Executors.newSingleThreadExecutor()
            val host = TravelLanTransport.host(true, loopback, 200)
            val player = Player()
            val session = session(player)
            val invite = TravelLanPairing.parse(host.invite.qrPayload())
            val serving = worker.submit { runCatching { host.serve(session) { it() } } }
            try {
                Socket(loopback, invite.port).use { socket ->
                    socket.soTimeout = 2000
                    if (ownerClose) {
                        val key = invite.key()
                        TravelSecureChannel(key, invite.linkId, false).use { channel ->
                            val hello = channel.seal("s20-lan-v1-ready".toByteArray())
                            val output = DataOutputStream(socket.getOutputStream())
                            output.writeInt(hello.size); output.write(hello); output.flush()
                            val input = java.io.DataInputStream(socket.getInputStream())
                            val size = input.readInt()
                            assertTrue(size in 28..65536)
                            channel.open(ByteArray(size).also { input.readFully(it) })
                        }
                        key.fill(0)
                        host.close()
                    }
                    // A close racing accept can reset the socket instead of sending EOF.
                    try { assertEquals(-1, socket.getInputStream().read()) } catch (_: java.net.SocketException) {}
                }
                serving.get(3, TimeUnit.SECONDS)
                disabled(session)
            } finally { invite.close(); host.close(); worker.shutdownNow() }
        }
    }
    @Test fun explicitOptInAndBoundedConfigurationRequired() {
        for (timeout in listOf(0, 99, 30001)) {
            try { TravelLanTransport.host(true, loopback, timeout); fail("timeout") } catch (_: IllegalArgumentException) {}
        }
        try { TravelLanTransport.host(false, loopback); fail("opt in") } catch (_: IllegalArgumentException) {}
        try { TravelLanTransport.host(true, InetAddress.getByName("0.0.0.0")); fail("wildcard") }
        catch (_: IllegalArgumentException) {}
    }
}
