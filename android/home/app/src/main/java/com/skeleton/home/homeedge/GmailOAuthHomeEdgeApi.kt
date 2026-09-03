package com.skeleton.home.homeedge

import com.skeleton.home.BuildConfig
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

data class GmailOAuthStartResult(val authorizationUrl: String, val message: String)
data class GmailOAuthCompleteResult(val message: String)

class GmailOAuthHomeEdgeApi(configuredBaseUrls: String = BuildConfig.HOME_EDGE_BASE_URLS) {
    private val baseUrls = configuredBaseUrls.split(',').map { it.trim().trimEnd('/') }.filter { it.isNotBlank() }

    fun start(): GmailOAuthStartResult = requestFirst("/api/mobile/gmail/oauth/start", null) { body ->
        val json = JSONObject(body)
        val url = json.optString("authorization_url").ifBlank { json.optString("url") }.trim()
        require(url.isNotBlank()) { "Home Edge не повернув адресу Google" }
        GmailOAuthStartResult(url, json.optString("message").ifBlank { "Відкрийте Google та завершіть вхід." })
    }

    fun complete(returnedUrl: String): GmailOAuthCompleteResult {
        val callback = returnedUrl.trim()
        require(callback.isNotBlank()) { "Вставте адресу після Google" }
        return requestFirst(
            "/api/mobile/gmail/oauth/callback-complete",
            JSONObject().put("returned_url", callback).toString(),
        ) { body ->
            val json = JSONObject(body)
            val status = json.optString("status").trim()
            val ok = when {
                json.has("ok") -> json.optBoolean("ok", false)
                status.isNotBlank() -> status.equals("ok", true) || status.equals("authorized", true) || status.equals("success", true)
                else -> false
            }
            check(ok) { json.optString("message").ifBlank { "Авторизацію Gmail не завершено" } }
            GmailOAuthCompleteResult(json.optString("message").ifBlank { "Gmail авторизовано" })
        }
    }

    private fun <T> requestFirst(path: String, payload: String?, decode: (String) -> T): T {
        check(baseUrls.isNotEmpty()) { "Home Edge не налаштований" }
        var last: Throwable? = null
        for (baseUrl in baseUrls) {
            try {
                val connection = URL(baseUrl + path).openConnection() as HttpURLConnection
                connection.requestMethod = "POST"
                connection.connectTimeout = 3000
                connection.readTimeout = 8000
                connection.setRequestProperty("Accept", "application/json")
                if (payload != null) {
                    connection.doOutput = true
                    connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                    connection.outputStream.bufferedWriter(Charsets.UTF_8).use { it.write(payload) }
                }
                try {
                    val code = connection.responseCode
                    val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                    val body = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
                    if (code !in 200..299) {
                        val message = runCatching { JSONObject(body).optString("message") }.getOrDefault("")
                        error(message.ifBlank { "Home Edge HTTP $code" })
                    }
                    return decode(body)
                } finally {
                    connection.disconnect()
                }
            } catch (error: Throwable) {
                last = error
            }
        }
        error(last?.message ?: "Home Edge недоступний")
    }
}
