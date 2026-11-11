package com.skeleton.home.travel

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.nio.ByteBuffer
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** Complete records only. A future foreground LAN adapter must bound reads to MAX_RECORD.
 * No implementation here opens sockets, discovers devices, pairs peers, or persists keys.
 */
fun interface TravelRecordExchange { fun exchange(request: ByteArray): ByteArray }

/** One link per explicitly provisioned peer/session. Supply a fresh random 32-byte key and
 * unique linkId for every connection from an approved pairing boundary. Never use a PIN as a key.
 * AES-GCM authenticates the peer possessing that key; separate directional keys prevent reflection.
 * Random nonce prefixes avoid nonce reuse even if a caller accidentally reuses provisioning.
 */
class TravelSecureChannel(key: ByteArray, private val linkId: String, host: Boolean) : AutoCloseable {
    companion object { const val MAX_RECORD = 65536 }
    private val sendKey: ByteArray
    private val receiveKey: ByteArray
    private val prefix = ByteArray(4).also { SecureRandom().nextBytes(it) }
    private var sent = 0L
    private var received = 0L
    private var closed = false
    private val sendDirection = if (host) "host" else "client"
    private val receiveDirection = if (host) "client" else "host"
    init {
        require(key.size == 32 && linkId.length in 1..128)
        fun derive(direction: String): ByteArray {
            val mac = Mac.getInstance("HmacSHA256")
            mac.init(SecretKeySpec(key.copyOf(), "HmacSHA256"))
            return mac.doFinal("s20-travel-v1:$linkId:$direction".toByteArray(Charsets.UTF_8))
        }
        sendKey = derive(sendDirection)
        receiveKey = derive(receiveDirection)
    }
    private fun cipher(mode: Int, key: ByteArray, nonce: ByteArray, direction: String): Cipher =
        Cipher.getInstance("AES/GCM/NoPadding").also {
            it.init(mode, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
            it.updateAAD("s20-travel-v1:$linkId:$direction".toByteArray(Charsets.UTF_8))
        }
    @Synchronized fun seal(payload: ByteArray): ByteArray {
        check(!closed && sent < Long.MAX_VALUE)
        require(payload.size <= MAX_RECORD - 28)
        val nonce = ByteBuffer.allocate(12).put(prefix).putLong(++sent).array()
        return nonce + cipher(Cipher.ENCRYPT_MODE, sendKey, nonce, sendDirection).doFinal(payload)
    }
    @Synchronized fun open(record: ByteArray): ByteArray {
        check(!closed)
        require(record.size in 28..MAX_RECORD) { "invalid_record" }
        val nonce = record.copyOfRange(0, 12)
        val sequence = ByteBuffer.wrap(nonce, 4, 8).long
        require(sequence == received + 1 && received < Long.MAX_VALUE) { "invalid_sequence" }
        // Advance only after authentication, so tampering cannot consume a valid sequence.
        val payload = cipher(Cipher.DECRYPT_MODE, receiveKey, nonce, receiveDirection)
            .doFinal(record, 12, record.size - 12)
        received = sequence
        return payload
    }
    @Synchronized override fun close() {
        closed = true
        sendKey.fill(0)
        receiveKey.fill(0)
    }
}

/** Fixed, bounded binary schema; no Java deserialization or remote origin field. */
internal object TravelWire {
    private fun encode(write: DataOutputStream.() -> Unit): ByteArray = ByteArrayOutputStream().let {
        DataOutputStream(it).use { out -> out.writeInt(1); write.invoke(out) }; it.toByteArray()
    }
    private fun <T> decode(bytes: ByteArray, read: DataInputStream.() -> T): T {
        require(bytes.size <= TravelSecureChannel.MAX_RECORD)
        return DataInputStream(ByteArrayInputStream(bytes)).use {
            require(it.readInt() == 1) { "unsupported_version" }
            val value = read.invoke(it)
            require(it.available() == 0) { "trailing_data" }
            value
        }
    }
    private fun DataOutputStream.media(media: TravelMedia?) {
        writeBoolean(media != null)
        if (media != null) {
            writeUTF(media.id); writeUTF(media.uri)
            writeBoolean(media.durationMs != null); media.durationMs?.let { writeLong(it) }
        }
    }
    private fun DataInputStream.media(): TravelMedia? = if (!readBoolean()) null else
        TravelMedia(readUTF(), readUTF(), if (readBoolean()) readLong() else null)
    fun command(command: TravelMediaCommand): ByteArray = encode {
        writeUTF(command.sessionId); writeLong(command.expectedRevision); writeUTF(command.action.name)
        media(command.media); writeBoolean(command.positionMs != null)
        command.positionMs?.let { writeLong(it) }
    }
    fun command(bytes: ByteArray): TravelMediaCommand = decode(bytes) {
        TravelMediaCommand(readUTF(), readLong(), TravelAction.valueOf(readUTF()), TravelOrigin.REMOTE,
            media(), if (readBoolean()) readLong() else null)
    }
    fun result(result: TravelCommandResult): ByteArray = encode {
        writeBoolean(result.accepted); writeUTF(result.reason ?: "")
        val state = result.state
        writeUTF(state.sessionId); writeLong(state.revision); media(state.media)
        writeUTF(state.output.name); writeBoolean(state.hdmiAvailable); writeUTF(state.playback.name)
        writeLong(state.positionMs); writeUTF(state.error ?: "")
    }
    fun result(bytes: ByteArray): TravelCommandResult = decode(bytes) {
        val accepted = readBoolean()
        val reason = readUTF().ifEmpty { null }
        val state = TravelSessionState(readUTF(), readLong(), media(), TravelOutput.valueOf(readUTF()),
            readBoolean(), TravelPlayback.valueOf(readUTF()), readLong(), readUTF().ifEmpty { null })
        TravelCommandResult(accepted, state, reason)
    }
}

/** Invoke on the media session's owning thread (Android: main thread). Only authenticated,
 * fully decoded requests reach the session. Close revokes remote control but leaves local playback.
 */
class TravelSecureHost(
    private val session: TravelMediaSession,
    private val channel: TravelSecureChannel,
) : TravelRecordExchange, AutoCloseable {
    private val owner = Any()
    private var closed = false
    init {
        if (!session.claimRemoteControl(owner)) {
            channel.close()
            throw IllegalStateException("remote_owner_unavailable")
        }
    }
    @Synchronized override fun exchange(request: ByteArray): ByteArray {
        check(!closed)
        try {
            val command = TravelWire.command(channel.open(request))
            return channel.seal(TravelWire.result(session.executeRemote(owner, command)))
        } catch (_: Exception) {
            close()
            throw IllegalStateException("travel_link_failed")
        }
    }
    @Synchronized override fun close() {
        if (closed) return
        closed = true
        channel.close()
        session.releaseRemoteControl(owner)
    }
}

/** Serial request/response client. Transport/authentication failures are terminal: create a new
 * provisioned link rather than retrying a command whose player effect may already have completed.
 */
class TravelSecureClient(
    private val sessionId: String,
    private val channel: TravelSecureChannel,
    private val transport: TravelRecordExchange,
) : AutoCloseable {
    private var closed = false
    @Synchronized fun execute(command: TravelMediaCommand): TravelCommandResult {
        check(!closed)
        require(command.sessionId == sessionId)
        try {
            val result = TravelWire.result(channel.open(transport.exchange(channel.seal(TravelWire.command(command)))))
            require(result.state.sessionId == sessionId)
            return result
        } catch (_: Exception) {
            close()
            throw IllegalStateException("travel_link_failed")
        }
    }
    @Synchronized override fun close() { closed = true; channel.close() }
}
