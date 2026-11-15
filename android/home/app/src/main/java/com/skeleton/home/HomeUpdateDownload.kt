package com.skeleton.home

import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.security.MessageDigest
import java.util.Locale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext

/** Runs in Home's network context; only a fully verified APK can leave this function. */
internal suspend fun downloadVerifiedHomeUpdate(
    url: String,
    outputDir: File,
    expectedSha256: String,
    expectedBytes: Long,
    connectTimeoutMs: Int = 8_000,
    readTimeoutMs: Int = 15_000,
    maxDurationMs: Long = 120_000,
    clockMs: () -> Long = { System.nanoTime() / 1_000_000 },
    onProgress: suspend (Int) -> Unit = {},
): File = withContext(Dispatchers.IO) {
    val target = URL(url)
    require(target.protocol in setOf("http", "https") && target.userInfo == null) {
        "Некоректна адреса оновлення"
    }
    require(expectedSha256.matches(Regex("[0-9a-fA-F]{64}"))) { "Некоректна SHA-256 оновлення" }
    require(expectedBytes in 1..(256L * 1024 * 1024)) { "Некоректний розмір оновлення" }
    require(connectTimeoutMs > 0 && readTimeoutMs > 0 && maxDurationMs > 0)
    currentCoroutineContext().ensureActive()
    if (!outputDir.isDirectory && !outputDir.mkdirs()) throw IOException("Не вдалося створити кеш оновлення")
    val partial = File.createTempFile("home-update-", ".part", outputDir)
    var connection: HttpURLConnection? = null
    var completed: File? = null
    var returned = false
    val started = clockMs()
    fun remainingMs(): Int {
        val remaining = maxDurationMs - (clockMs() - started)
        if (remaining <= 0) throw SocketTimeoutException("Час завантаження оновлення вичерпано")
        return remaining.coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
    }
    try {
        onProgress(0)
        val conn = target.openConnection() as HttpURLConnection
        connection = conn
        conn.connectTimeout = minOf(connectTimeoutMs, remainingMs())
        conn.readTimeout = minOf(readTimeoutMs, remainingMs())
        conn.useCaches = false
        conn.setRequestProperty("Accept", "application/vnd.android.package-archive")
        val code = conn.responseCode
        currentCoroutineContext().ensureActive()
        remainingMs()
        if (code != HttpURLConnection.HTTP_OK) throw IOException("Сервер оновлення відповів HTTP " + code)
        val announced = conn.contentLengthLong
        if (announced >= 0 && announced != expectedBytes) throw IOException("Розмір APK не збігається з маніфестом")
        val digest = MessageDigest.getInstance("SHA-256")
        var downloaded = 0L
        var lastProgress = 0
        conn.inputStream.use { input ->
            partial.outputStream().use { output ->
                val buffer = ByteArray(64 * 1024)
                while (true) {
                    currentCoroutineContext().ensureActive()
                    conn.readTimeout = minOf(readTimeoutMs, remainingMs())
                    val count = input.read(buffer)
                    if (count < 0) break
                    if (count == 0) continue
                    downloaded += count
                    if (downloaded > expectedBytes) throw IOException("APK перевищує очікуваний розмір")
                    output.write(buffer, 0, count)
                    digest.update(buffer, 0, count)
                    val progress = ((downloaded * 100L) / expectedBytes).coerceAtMost(99).toInt()
                    if (progress != lastProgress) {
                        lastProgress = progress
                        onProgress(progress)
                    }
                }
            }
        }
        currentCoroutineContext().ensureActive()
        remainingMs()
        if (downloaded != expectedBytes) throw IOException("APK завантажено не повністю. Спробуйте ще раз")
        val actual = digest.digest().joinToString("") { "%02x".format(it.toInt() and 0xff) }
        if (actual != expectedSha256.lowercase(Locale.ROOT)) throw IOException("SHA-256 оновлення не збігається")
        val verified = File(outputDir, partial.name.removeSuffix(".part") + ".apk")
        if (!partial.renameTo(verified)) throw IOException("Не вдалося зберегти перевірений APK")
        completed = verified
        onProgress(100)
        currentCoroutineContext().ensureActive()
        returned = true
        verified
    } finally {
        connection?.disconnect()
        partial.delete()
        if (!returned) completed?.delete()
    }
}
