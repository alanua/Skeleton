package com.skeleton.home.travel

import java.net.URI

/** Source-only Samsung S20 Travel foundation. No discovery, pairing or UI side effects. */
enum class TravelOutput { PHONE, HDMI }
enum class TravelPlayback { IDLE, READY, PLAYING, PAUSED, STOPPED, ERROR }
enum class TravelAction { LOAD, PLAY, PAUSE, SEEK, STOP }
enum class TravelOrigin { LOCAL, REMOTE }

data class TravelMedia(val id: String, val uri: String, val durationMs: Long? = null) {
    init {
        require(id.isNotBlank())
        val parsed = URI(uri)
        require(parsed.scheme == "content" || parsed.scheme == "https")
        require(!parsed.rawAuthority.isNullOrBlank() && parsed.rawUserInfo == null)
        require(durationMs == null || durationMs >= 0)
    }
}

data class TravelSessionState(
    val sessionId: String,
    val revision: Long = 0,
    val media: TravelMedia? = null,
    val output: TravelOutput = TravelOutput.PHONE,
    val hdmiAvailable: Boolean = false,
    val playback: TravelPlayback = TravelPlayback.IDLE,
    val positionMs: Long = 0,
    val error: String? = null,
)

data class TravelMediaCommand(
    val sessionId: String,
    val expectedRevision: Long,
    val action: TravelAction,
    val origin: TravelOrigin = TravelOrigin.LOCAL,
    val media: TravelMedia? = null,
    val positionMs: Long? = null,
)

/** Implementations own player lifecycle and HDMI surface binding; methods must complete synchronously.
 * Throwing implementations must allow stop() to attempt cleanup after a partial operation.
 */
interface TravelLocalPlayer {
    fun load(media: TravelMedia, output: TravelOutput)
    fun play()
    fun pause()
    fun seekTo(positionMs: Long)
    fun stop()
}

/** A transport must authenticate its peer before constructing commands; this port grants no trust. */
interface TravelRemoteSessionPort {
    fun publish(state: TravelSessionState)
}

data class TravelCommandResult(val accepted: Boolean, val state: TravelSessionState, val reason: String? = null)

/** Serializes local and remote commands against one authoritative phone-side player.
 * Remote access is disabled by default and explicitly revoked on close. No TV APIs are used.
 * Published state acknowledges completed player calls, not independently observed hardware playback.
 */
class TravelMediaSession(
    sessionId: String,
    private val player: TravelLocalPlayer,
    private val remote: TravelRemoteSessionPort,
) {
    init { require(sessionId.isNotBlank()) }

    private var current = TravelSessionState(sessionId)
    private var remoteEnabled = false
    private var closed = false

    @Synchronized fun snapshot(): TravelSessionState = current

    @Synchronized fun allowRemoteControl(enabled: Boolean) {
        check(!closed)
        remoteEnabled = enabled
    }

    @Synchronized fun execute(command: TravelMediaCommand): TravelCommandResult {
        if (closed) return reject("closed")
        if (command.sessionId != current.sessionId) return reject("wrong_session")
        if (command.origin == TravelOrigin.REMOTE && !remoteEnabled) return reject("remote_disabled")
        if (command.expectedRevision != current.revision) return reject("stale_revision")
        if (command.action != TravelAction.LOAD && command.media != null) return reject("unexpected_media")
        if (command.action != TravelAction.SEEK && command.positionMs != null) return reject("unexpected_position")
        if (command.action == TravelAction.LOAD && command.media == null) return reject("missing_media")
        if (command.action == TravelAction.SEEK && (command.positionMs == null || command.positionMs < 0)) {
            return reject("invalid_position")
        }
        if (command.action != TravelAction.LOAD && command.action != TravelAction.STOP &&
            (current.media == null || current.playback in setOf(TravelPlayback.ERROR, TravelPlayback.STOPPED))) {
            return reject("media_not_ready")
        }
        return try {
            val next = when (command.action) {
                TravelAction.LOAD -> {
                    val media = requireNotNull(command.media)
                    player.load(media, current.output)
                    current.copy(media = media, playback = TravelPlayback.READY, positionMs = 0, error = null)
                }
                TravelAction.PLAY -> {
                    player.play()
                    current.copy(playback = TravelPlayback.PLAYING)
                }
                TravelAction.PAUSE -> {
                    player.pause()
                    current.copy(playback = TravelPlayback.PAUSED)
                }
                TravelAction.SEEK -> {
                    val requested = requireNotNull(command.positionMs)
                    val position = current.media?.durationMs?.let { requested.coerceAtMost(it) } ?: requested
                    player.seekTo(position)
                    current.copy(positionMs = position)
                }
                TravelAction.STOP -> {
                    player.stop()
                    current.copy(playback = TravelPlayback.STOPPED, positionMs = 0, error = null)
                }
            }
            commit(next)
            TravelCommandResult(true, current)
        } catch (_: Exception) {
            failPlayer()
            reject("player_failure")
        }
    }

    /** Explicit output selection never starts playback. Changing surfaces stops the old player. */
    @Synchronized fun selectOutput(output: TravelOutput): TravelCommandResult {
        if (closed) return reject("closed")
        if (output == TravelOutput.HDMI && !current.hdmiAvailable) return reject("hdmi_unavailable")
        if (output == current.output) return TravelCommandResult(true, current)
        return try {
            player.stop()
            commit(current.copy(output = output, media = null, positionMs = 0,
                playback = TravelPlayback.IDLE, error = null))
            TravelCommandResult(true, current)
        } catch (_: Exception) {
            failPlayer()
            reject("player_failure")
        }
    }

    /** Called by a future Android display adapter. Disconnect never resumes on the phone. */
    @Synchronized fun updateHdmiAvailability(available: Boolean): TravelCommandResult {
        if (closed) return reject("closed")
        if (available == current.hdmiAvailable) return TravelCommandResult(true, current)
        if (!available && current.output == TravelOutput.HDMI) {
            try {
                player.stop()
            } catch (_: Exception) {
                current = current.copy(hdmiAvailable = false, output = TravelOutput.PHONE, media = null)
                failPlayer()
                return reject("player_failure")
            }
            commit(current.copy(hdmiAvailable = false, output = TravelOutput.PHONE,
                media = null, playback = TravelPlayback.IDLE, positionMs = 0, error = null))
        } else {
            commit(current.copy(hdmiAvailable = available))
        }
        return TravelCommandResult(true, current)
    }

    @Synchronized fun close() {
        if (closed) return
        closed = true
        remoteEnabled = false
        try {
            player.stop()
            commit(current.copy(media = null, playback = TravelPlayback.STOPPED, positionMs = 0, error = null))
        } catch (_: Exception) {
            failPlayer()
        }
    }

    private fun reject(reason: String) = TravelCommandResult(false, current, reason)

    private fun failPlayer() {
        runCatching { player.stop() }
        commit(current.copy(playback = TravelPlayback.ERROR, positionMs = 0, error = "player_failure"))
    }

    private fun commit(next: TravelSessionState) {
        current = next.copy(revision = current.revision + 1)
        // A disconnected observer cannot roll back a completed local player operation.
        runCatching { remote.publish(current) }
    }
}
