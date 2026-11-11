package com.skeleton.home.travel

import org.junit.Assert.*
import org.junit.Test

class TravelSecureLinkTest {
    private val key = ByteArray(32) { it.toByte() } // Synthetic only.
    private class Player : TravelLocalPlayer {
        val calls = mutableListOf<String>()
        override fun load(media: TravelMedia, output: TravelOutput) { calls += "load" }
        override fun play() { calls += "play" }
        override fun pause() { calls += "pause" }
        override fun seekTo(positionMs: Long) { calls += "seek:$positionMs" }
        override fun stop() { calls += "stop" }
    }
    private fun fails(block: () -> Unit) {
        try { block(); fail("expected rejection") } catch (_: IllegalStateException) {
        } catch (_: IllegalArgumentException) {
        } catch (_: java.security.GeneralSecurityException) {}
    }
    @Test fun secureHostFailsClosedUntilExplicitlyOptedIn() {
        val session = TravelMediaSession("synthetic", Player(), object : TravelRemoteSessionPort {
            override fun publish(state: TravelSessionState) {}
        })
        val channel = TravelSecureChannel(key, "denied", true)
        fails { TravelSecureHost(session, channel) }
        fails { channel.seal(byteArrayOf()) }

        session.allowRemoteControl(true)
        val host = TravelSecureHost(session, TravelSecureChannel(key, "granted", true))
        fails { TravelSecureHost(session, TravelSecureChannel(key, "consumed", true)) }
        host.close()
    }
    @Test fun encryptedRoundTripUsesAuthoritativePlayerAndRevision() {
        val player = Player()
        val session = TravelMediaSession("synthetic", player, object : TravelRemoteSessionPort {
            override fun publish(state: TravelSessionState) {}
        })
        session.allowRemoteControl(true)
        val host = TravelSecureHost(session, TravelSecureChannel(key, "link", true))
        val client = TravelSecureClient("synthetic", TravelSecureChannel(key, "link", false), host)
        val load = TravelMediaCommand("synthetic", 0, TravelAction.LOAD,
            media = TravelMedia("demo", "content://synthetic/demo", 100))
        assertEquals(1L, client.execute(load).state.revision)
        assertEquals("stale_revision", client.execute(load).reason)
        assertTrue(client.execute(TravelMediaCommand("synthetic", 1, TravelAction.PLAY)).accepted)
        assertEquals(100L, client.execute(TravelMediaCommand("synthetic", 2, TravelAction.SEEK,
            positionMs = 500)).state.positionMs)
        assertEquals(listOf("load", "play", "seek:100"), player.calls)
        host.close()
        assertEquals("remote_disabled", session.execute(TravelMediaCommand("synthetic", 3,
            TravelAction.PAUSE, TravelOrigin.REMOTE)).reason)
        fails { client.execute(TravelMediaCommand("synthetic", 3, TravelAction.STOP)) }
        fails { client.execute(load) }
    }
    @Test fun authenticationReplayReflectionAndBounds() {
        val client = TravelSecureChannel(key, "link", false)
        val host = TravelSecureChannel(key, "link", true)
        val record = client.seal("synthetic".toByteArray())
        val tampered = record.copyOf().also { it[it.lastIndex] = (it.last().toInt() xor 1).toByte() }
        fails { host.open(tampered) }
        assertEquals("synthetic", String(host.open(record)))
        fails { host.open(record) }
        fails { client.open(record) }
        fails { TravelSecureChannel(ByteArray(32) { 42 }, "link", true).open(record) }
        fails { TravelSecureChannel(key, "different", true).open(record) }
        fails { host.open(ByteArray(TravelSecureChannel.MAX_RECORD + 1)) }
        fails { client.seal(ByteArray(TravelSecureChannel.MAX_RECORD)) }
        client.close()
        fails { client.seal(byteArrayOf()) }
    }
    @Test fun malformedAuthenticatedCommandClosesHostBeforePlayerEffects() {
        val player = Player()
        val session = TravelMediaSession("synthetic", player, object : TravelRemoteSessionPort {
            override fun publish(state: TravelSessionState) {}
        })
        session.allowRemoteControl(true)
        val host = TravelSecureHost(session, TravelSecureChannel(key, "link", true))
        val peer = TravelSecureChannel(key, "link", false)
        fails { host.exchange(peer.seal(byteArrayOf(0))) }
        assertTrue(player.calls.isEmpty())
        assertEquals("remote_disabled", session.execute(TravelMediaCommand("synthetic", 0,
            TravelAction.STOP, TravelOrigin.REMOTE)).reason)
        fails { host.exchange(peer.seal(TravelWire.command(TravelMediaCommand("synthetic", 0, TravelAction.STOP)))) }
    }

    @Test fun overlappingAuthenticatedHostIsRejectedAndStaleCloseCannotRevokeNewOwner() {
        val session = TravelMediaSession("synthetic", Player(), object : TravelRemoteSessionPort {
            override fun publish(state: TravelSessionState) {}
        })
        session.allowRemoteControl(true)
        val old = TravelSecureHost(session, TravelSecureChannel(key, "old", true))
        fails { session.allowRemoteControl(true) }
        fails { TravelSecureHost(session, TravelSecureChannel(key, "overlap", true)) }
        old.close()

        session.allowRemoteControl(true)
        val current = TravelSecureHost(session, TravelSecureChannel(key, "current", true))
        old.close()
        fails { old.exchange(ByteArray(28)) }
        val client = TravelSecureClient("synthetic", TravelSecureChannel(key, "current", false), current)
        assertTrue(client.execute(TravelMediaCommand("synthetic", 0, TravelAction.STOP)).accepted)
        current.close()
        assertEquals("remote_disabled", session.execute(TravelMediaCommand("synthetic", 1,
            TravelAction.STOP, TravelOrigin.REMOTE)).reason)
        fails { client.execute(TravelMediaCommand("synthetic", 1, TravelAction.STOP)) }
    }

    @Test fun explicitRevokeBlocksStaleLinkAndOldCleanupPreservesFreshOwner() {
        val session = TravelMediaSession("synthetic", Player(), object : TravelRemoteSessionPort {
            override fun publish(state: TravelSessionState) {}
        })
        session.allowRemoteControl(true)
        val oldHost = TravelSecureHost(session, TravelSecureChannel(key, "old-revoked", true))
        val oldClient = TravelSecureClient("synthetic",
            TravelSecureChannel(key, "old-revoked", false), oldHost)
        session.allowRemoteControl(false)
        assertEquals("remote_disabled", oldClient.execute(TravelMediaCommand(
            "synthetic", 0, TravelAction.STOP)).reason)

        session.allowRemoteControl(true)
        val newHost = TravelSecureHost(session, TravelSecureChannel(key, "new-owner", true))
        val newClient = TravelSecureClient("synthetic",
            TravelSecureChannel(key, "new-owner", false), newHost)
        oldHost.close()
        assertTrue(newClient.execute(TravelMediaCommand("synthetic", 0, TravelAction.STOP)).accepted)
        newClient.close()
        newHost.close()
        oldClient.close()
    }
}
