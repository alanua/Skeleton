package com.skeleton.home.travel

import java.io.DataInputStream
import java.io.DataOutputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Blocking, foreground-owned transport. No auto-start, discovery, reconnect, or device operations.
 * The caller must bind the explicit address of its private WiFi network (never a wildcard),
 * run serve/connect/exchange on a worker, and close when leaving the foreground/network.
 */
class TravelLanTransport private constructor(private val socket: Socket, private val timeoutMs: Int) :
    TravelRecordExchange, AutoCloseable {
    private val timer = Executors.newSingleThreadScheduledExecutor { task ->
        Thread(task, "travel-lan-deadline").also { it.isDaemon = true }
    }
    private val input = DataInputStream(socket.getInputStream())
    private val output = DataOutputStream(socket.getOutputStream())
    @Volatile private var closed = false

    private fun <T> bounded(block: () -> T): T {
        check(!closed)
        val deadline = timer.schedule({ close() }, timeoutMs.toLong(), TimeUnit.MILLISECONDS)
        try { return block() } catch (_: Exception) {
            close()
            throw IllegalStateException("travel_lan_failed")
        } finally { deadline.cancel(false) }
    }
    private fun read(): ByteArray {
        val size = input.readInt()
        require(size in 28..TravelSecureChannel.MAX_RECORD) { "invalid_frame" }
        return ByteArray(size).also { input.readFully(it) }
    }
    private fun write(record: ByteArray) {
        require(record.size in 28..TravelSecureChannel.MAX_RECORD)
        output.writeInt(record.size)
        output.write(record)
        output.flush()
    }
    @Synchronized override fun exchange(request: ByteArray): ByteArray = bounded { write(request); read() }
    internal fun serve(exchange: TravelRecordExchange) {
        while (!closed) bounded { val request = read(); write(exchange.exchange(request)) }
    }
    override fun close() {
        closed = true
        runCatching { socket.close() }
        timer.shutdownNow()
    }

    /** Single-use listener: any peer attempt consumes the invite, including authentication failure.
     * dispatch must synchronously marshal BOTH construction/exchange and cleanup to the session's
     * owning thread. It must be bounded and must never wait for the network worker on that thread.
     */
    class Host internal constructor(
        address: InetAddress,
        private val timeoutMs: Int,
        private val remoteControlOptIn: Boolean,
    ) : AutoCloseable {
        private val listener = ServerSocket()
        val invite: TravelLanPairing
        @Volatile private var connection: TravelLanTransport? = null
        @Volatile private var closed = false
        private var used = false
        init {
            try {
                listener.soTimeout = timeoutMs
                listener.bind(InetSocketAddress(address, 0), 1)
                invite = TravelLanPairing.create(address, listener.localPort)
            } catch (e: Exception) { listener.close(); throw e }
        }
        fun serve(session: TravelMediaSession, dispatch: (() -> Unit) -> Unit) {
            synchronized(this) { check(!closed && !used); used = true }
            var secure: TravelSecureHost? = null
            var channel: TravelSecureChannel? = null
            var key: ByteArray? = null
            try {
                val provisioned = invite.key()
                key = provisioned
                val socket = listener.accept()
                listener.close()
                synchronized(this) {
                    if (closed) { socket.close(); error("closed") }
                    connection = TravelLanTransport(socket, timeoutMs)
                }
                require(TravelLanPairing.allowed(socket.inetAddress))
                val active = TravelSecureChannel(provisioned, invite.linkId, true)
                channel = active
                // Explicit proof of key possession before any command dispatch.
                connection!!.bounded {
                    require(active.open(connection!!.read()).contentEquals(HELLO))
                    connection!!.write(active.seal(HELLO))
                }
                dispatch {
                    check(remoteControlOptIn)
                    secure = TravelSecureHost(session, active)
                }
                connection!!.serve(TravelRecordExchange { request ->
                    var response: ByteArray? = null
                    dispatch { response = secure!!.exchange(request) }
                    requireNotNull(response)
                })
            } finally {
                close()
                key?.fill(0)
                try { dispatch { secure?.close() } }
                finally { channel?.close() }
            }
        }
        override fun close() {
            synchronized(this) { closed = true; runCatching { listener.close() }; connection?.close(); invite.close() }
        }
    }
    companion object {
        private val HELLO = "s20-lan-v1-ready".toByteArray(Charsets.UTF_8)
        fun host(optIn: Boolean, address: InetAddress, timeoutMs: Int = 10000): Host {
            require(optIn && TravelLanPairing.allowed(address) && timeoutMs in 100..30000)
            return Host(address, timeoutMs, optIn)
        }
        fun connect(optIn: Boolean, invite: TravelLanPairing, sessionId: String,
                    timeoutMs: Int = 10000): AutoCloseableClient {
            require(optIn && timeoutMs in 100..30000)
            val key = invite.key()
            val socket = Socket()
            var transport: TravelLanTransport? = null
            var channel: TravelSecureChannel? = null
            try {
                socket.connect(InetSocketAddress(invite.address, invite.port), timeoutMs)
                transport = TravelLanTransport(socket, timeoutMs)
                channel = TravelSecureChannel(key, invite.linkId, false)
                require(channel.open(transport.exchange(channel.seal(HELLO))).contentEquals(HELLO))
                return AutoCloseableClient(TravelSecureClient(sessionId, channel, transport), transport)
            } catch (_: Exception) {
                channel?.close(); transport?.close(); socket.close()
                throw IllegalStateException("travel_lan_failed")
            } finally { key.fill(0); invite.close() }
        }
    }
    class AutoCloseableClient internal constructor(private val client: TravelSecureClient,
                                                   private val transport: TravelLanTransport) : AutoCloseable {
        @Synchronized fun execute(command: TravelMediaCommand): TravelCommandResult = try {
            client.execute(command)
        } catch (_: Exception) { close(); throw IllegalStateException("travel_lan_failed") }
        override fun close() { transport.close(); client.close() }
    }
}
