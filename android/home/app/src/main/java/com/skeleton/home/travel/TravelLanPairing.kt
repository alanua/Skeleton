package com.skeleton.home.travel

import java.net.InetAddress
import java.security.SecureRandom
import java.util.Base64

/** In-memory, out-of-band QR payload. Never log, persist, or put in an Android intent. */
class TravelLanPairing private constructor(
    val address: InetAddress, val port: Int, val linkId: String, private val secret: ByteArray,
) : AutoCloseable {
    private var closed = false
    @Synchronized fun qrPayload(): String {
        check(!closed)
        return "s20lan:1:${address.hostAddress}:$port:$linkId:${Base64.getUrlEncoder().withoutPadding().encodeToString(secret)}"
    }
    @Synchronized internal fun key(): ByteArray { check(!closed); return secret.copyOf() }
    @Synchronized override fun close() { closed = true; secret.fill(0) }
    override fun toString() = "TravelLanPairing(redacted)"

    companion object {
        internal fun allowed(address: InetAddress): Boolean {
            val b = address.address
            if (b.size != 4) return false
            val first = b[0].toInt().and(255)
            val second = b[1].toInt().and(255)
            return address.isLoopbackAddress || first == 10 ||
                (first == 172 && second in 16..31) || (first == 192 && second == 168)
        }
        internal fun create(address: InetAddress, port: Int): TravelLanPairing {
            require(allowed(address) && port in 1..65535)
            val random = SecureRandom()
            val id = ByteArray(16).also { random.nextBytes(it) }
            return TravelLanPairing(address, port, id.joinToString("") { "%02x".format(it) },
                ByteArray(32).also { random.nextBytes(it) })
        }
        fun parse(payload: String): TravelLanPairing {
            require(payload.length <= 180) { "invalid_invite" }
            val parts = payload.split(':')
            require(parts.size == 6 && parts[0] == "s20lan" && parts[1] == "1") { "invalid_invite" }
            // Numeric IPv4 only: parsing cannot trigger DNS or accept a public destination.
            val octets = parts[2].split('.')
            require(octets.size == 4 && octets.all { it.matches(Regex("0|[1-9][0-9]{0,2}")) && it.toInt() <= 255 })
            val address = InetAddress.getByAddress(octets.map { it.toInt().toByte() }.toByteArray())
            val port = parts[3].toIntOrNull()
            require(allowed(address) && port != null && port in 1..65535)
            require(parts[4].matches(Regex("[0-9a-f]{32}")) && parts[5].matches(Regex("[A-Za-z0-9_-]{43}")))
            val key = Base64.getUrlDecoder().decode(parts[5])
            require(key.size == 32 && Base64.getUrlEncoder().withoutPadding().encodeToString(key) == parts[5])
            return TravelLanPairing(address, port, parts[4], key)
        }
    }
}
