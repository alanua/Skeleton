package com.skeleton.home.storage

import com.skeleton.home.domain.SecureStorage

class PlaceholderSecureStorage : SecureStorage {
    private val values = linkedMapOf<String, ByteArray>()

    override suspend fun putOpaqueValue(key: String, value: ByteArray) {
        values[key] = value.copyOf()
    }

    override suspend fun readOpaqueValue(key: String): ByteArray? =
        values[key]?.copyOf()

    override suspend fun remove(key: String) {
        values.remove(key)
    }
}
