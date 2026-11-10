package com.skeleton.home.travel

import org.junit.Assert.*
import org.junit.Test

class TravelMediaSessionTest {
    private class Player : TravelLocalPlayer {
        val calls = mutableListOf<String>()
        var fail = false
        override fun load(media: TravelMedia, output: TravelOutput) { calls += "load:$output" }
        override fun play() { if (fail) error("synthetic"); calls += "play" }
        override fun pause() { calls += "pause" }
        override fun seekTo(positionMs: Long) { calls += "seek:$positionMs" }
        override fun stop() { calls += "stop" }
    }

    @Test fun localAndRemoteShareRevisionAndPlayer() {
        val player = Player()
        val session = TravelMediaSession("synthetic", player, object : TravelRemoteSessionPort {
            override fun publish(state: TravelSessionState) { error("offline") }
        })
        val media = TravelMedia("demo", "content://synthetic/demo", 100)
        assertTrue(session.execute(TravelMediaCommand("synthetic", 0, TravelAction.LOAD, media = media)).accepted)
        val play = TravelMediaCommand("synthetic", 1, TravelAction.PLAY, TravelOrigin.REMOTE)
        assertEquals("remote_disabled", session.execute(play).reason)
        session.allowRemoteControl(true)
        assertTrue(session.execute(play).accepted)
        assertEquals("stale_revision", session.execute(play).reason)
        assertTrue(session.execute(TravelMediaCommand("synthetic", 2, TravelAction.SEEK, positionMs = 200)).accepted)
        assertEquals(100L, session.snapshot().positionMs)
        assertEquals(listOf("load:PHONE", "play", "seek:100"), player.calls)
    }

    @Test fun hdmiDisconnectStopsWithoutAutoplayAndCloseRevokesCommands() {
        val player = Player()
        val session = TravelMediaSession("synthetic", player, object : TravelRemoteSessionPort {
            override fun publish(state: TravelSessionState) {}
        })
        assertEquals("hdmi_unavailable", session.selectOutput(TravelOutput.HDMI).reason)
        session.updateHdmiAvailability(true)
        session.selectOutput(TravelOutput.HDMI)
        session.execute(TravelMediaCommand("synthetic", 2, TravelAction.LOAD,
            media = TravelMedia("demo", "https://example.test/demo")))
        session.execute(TravelMediaCommand("synthetic", 3, TravelAction.PLAY))
        session.updateHdmiAvailability(false)
        assertEquals(TravelOutput.PHONE, session.snapshot().output)
        assertEquals(TravelPlayback.IDLE, session.snapshot().playback)
        assertNull(session.snapshot().media)
        assertEquals("stop", player.calls.last())
        session.close()
        assertEquals("closed", session.execute(TravelMediaCommand("synthetic", 6, TravelAction.PLAY)).reason)
    }

    @Test fun playerFailureIsSanitizedAndRequiresReload() {
        val player = Player()
        val session = TravelMediaSession("synthetic", player, object : TravelRemoteSessionPort {
            override fun publish(state: TravelSessionState) {}
        })
        session.execute(TravelMediaCommand("synthetic", 0, TravelAction.LOAD,
            media = TravelMedia("demo", "content://synthetic/demo")))
        player.fail = true
        assertEquals("player_failure", session.execute(TravelMediaCommand("synthetic", 1, TravelAction.PLAY)).reason)
        assertEquals(TravelPlayback.ERROR, session.snapshot().playback)
        assertEquals("media_not_ready", session.execute(TravelMediaCommand("synthetic", 2, TravelAction.PLAY)).reason)
        assertEquals("stop", player.calls.last())
    }
    @Test fun asynchronousRendererFailureStopsAndRequiresReload() {
        val player = Player()
        val session = TravelMediaSession("synthetic", player, object : TravelRemoteSessionPort {
            override fun publish(state: TravelSessionState) {}
        })
        session.execute(TravelMediaCommand("synthetic", 0, TravelAction.LOAD,
            media = TravelMedia("demo", "content://synthetic/demo")))
        session.rendererFailed()
        assertEquals(TravelPlayback.ERROR, session.snapshot().playback)
        assertEquals("stop", player.calls.last())
        assertEquals("media_not_ready", session.execute(TravelMediaCommand(
            "synthetic", session.snapshot().revision, TravelAction.PLAY)).reason)
        session.close()
        val terminal = session.snapshot()
        session.rendererFailed()
        assertEquals(terminal, session.snapshot())
    }

}
