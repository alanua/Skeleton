package com.skeleton.home.homeedge

import com.skeleton.home.BuildConfig
import com.skeleton.home.domain.ConnectivityStatus
import com.skeleton.home.domain.VerifiedActionState
import java.net.HttpURLConnection
import java.net.URL
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

enum class SharpTvAction(val wireValue: String) {
    HDMI1("hdmi1"),
    HDMI2("hdmi2"),
    HDMI3("hdmi3"),
    UP("up"),
    DOWN("down"),
    LEFT("left"),
    RIGHT("right"),
    OK("ok"),
    BACK("back"),
    MENU("menu"),
    STANDBY("standby"),
    POWER_ON("power_on"),
}

data class SharpTvStatus(
    val connectivity: ConnectivityStatus,
    val reachable: Boolean,
    val currentSource: String?,
    val supportsHdmi3: Boolean,
    val powerOnAvailable: Boolean,
    val actionState: VerifiedActionState? = null,
)

data class SharpTvActionResult(
    val state: VerifiedActionState,
    val message: String,
)

class SharpManagedDisplayHomeEdgeApi(
    configuredBaseUrls: String = BuildConfig.HOME_EDGE_BASE_URLS,
    private val transport: (method: String, baseUrl: String, path: String, payload: String?) -> String = ::httpRequest,
) {
    private val baseUrls = configuredBaseUrls
        .split(',')
        .map { it.trim().trimEnd('/') }
        .filter { it.isNotBlank() }

    suspend fun status(): SharpTvStatus = withContext(Dispatchers.IO) {
        requestFirst("GET", "/api/managed-display/sharp/status", null) { body ->
            val json = JSONObject(body)
            val capabilities = json.optJSONObject("capabilities") ?: JSONObject()
            val reachable = json.optBoolean("reachable", false)
            SharpTvStatus(
                connectivity = parseConnectivity(json.optString("connectivity"), reachable),
                reachable = reachable,
                currentSource = json.optString("current_source").trim().ifBlank { null },
                supportsHdmi3 = capabilities.optBoolean("hdmi3", false),
                powerOnAvailable = capabilities.optBoolean("power_on_physically_verified", false),
                actionState = parseActionState(json.optString("action_state")),
            )
        }
    }

    suspend fun action(action: SharpTvAction): SharpTvActionResult = withContext(Dispatchers.IO) {
        requestFirst(
            "POST",
            "/api/managed-display/sharp/action",
            JSONObject().put("action", action.wireValue).toString(),
        ) { body ->
            val json = JSONObject(body)
            val state = parseActionState(json.optString("state"))
                ?: error("Home Edge не повернув стан дії")
            SharpTvActionResult(
                state = state,
                message = json.optString("message").ifBlank { state.defaultMessage() },
            )
        }
    }

    private fun <T> requestFirst(
        method: String,
        path: String,
        payload: String?,
        decode: (String) -> T,
    ): T {
        check(baseUrls.isNotEmpty()) { "Home Edge не налаштований" }
        var last: Throwable? = null
        for (baseUrl in baseUrls) {
            try {
                return decode(transport(method, baseUrl, path, payload))
            } catch (error: Throwable) {
                last = error
            }
        }
        error(last?.message ?: "Home Edge недоступний")
    }

    companion object {
        private fun parseConnectivity(raw: String, reachable: Boolean): ConnectivityStatus =
            runCatching { ConnectivityStatus.valueOf(raw.trim().uppercase()) }
                .getOrElse { if (reachable) ConnectivityStatus.ONLINE else ConnectivityStatus.OFFLINE }

        private fun parseActionState(raw: String): VerifiedActionState? =
            raw.trim().takeIf { it.isNotBlank() }?.let { value ->
                runCatching { VerifiedActionState.valueOf(value.uppercase()) }.getOrNull()
            }

        private fun VerifiedActionState.defaultMessage(): String = when (this) {
            VerifiedActionState.SENT -> "Команду надіслано"
            VerifiedActionState.ACCEPTED -> "Команду прийнято"
            VerifiedActionState.APPLIED -> "Команду застосовано"
            VerifiedActionState.PHYSICALLY_VERIFIED -> "Результат підтверджено"
        }

        private fun httpRequest(method: String, baseUrl: String, path: String, payload: String?): String {
            val connection = URL(baseUrl + path).openConnection() as HttpURLConnection
            connection.requestMethod = method
            connection.connectTimeout = 3000
            connection.readTimeout = 5000
            connection.setRequestProperty("Accept", "application/json")
            if (payload != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                connection.outputStream.bufferedWriter(Charsets.UTF_8).use { it.write(payload) }
            }
            return try {
                val code = connection.responseCode
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                val body = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
                if (code !in 200..299) {
                    val message = runCatching { JSONObject(body).optString("message") }.getOrDefault("")
                    error(message.ifBlank { "Home Edge HTTP $code" })
                }
                body
            } finally {
                connection.disconnect()
            }
        }
    }
}
