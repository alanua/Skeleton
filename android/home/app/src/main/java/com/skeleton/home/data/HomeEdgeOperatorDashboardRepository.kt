package com.skeleton.home.data

import com.skeleton.home.domain.OperatorDashboardSection
import com.skeleton.home.domain.OperatorLiveState
import com.skeleton.home.domain.OperatorLiveStateStatus
import java.net.HttpURLConnection
import java.net.URL
import java.time.Clock
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

class HomeEdgeOperatorDashboardRepository(
    private val endpointConfig: HomeEdgeEndpointConfig,
    private val clock: Clock = Clock.systemUTC(),
    private val connectTimeoutMillis: Int = 2_500,
    private val readTimeoutMillis: Int = 2_500,
) {
    suspend fun loadLiveState(): OperatorLiveState = withContext(Dispatchers.IO) {
        runCatching { fetchLiveState() }.getOrElse { error ->
            OperatorLiveState.offline(
                reason = error.message?.takeIf { it.isNotBlank() } ?: "endpoint_unreachable",
                checkedAtEpochSeconds = nowSeconds(),
            )
        }
    }

    private fun fetchLiveState(): OperatorLiveState {
        val connection = (URL(endpointConfig.liveStateUrl).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = connectTimeoutMillis
            readTimeout = readTimeoutMillis
            setRequestProperty("Accept", "application/json")
        }
        try {
            val statusCode = connection.responseCode
            if (statusCode !in 200..299) {
                return OperatorLiveState.offline(
                    reason = "endpoint_http_$statusCode",
                    checkedAtEpochSeconds = nowSeconds(),
                )
            }
            val body = connection.inputStream.bufferedReader(Charsets.UTF_8).use { it.readText() }
            return parseLiveState(body)
        } finally {
            connection.disconnect()
        }
    }

    private fun parseLiveState(body: String): OperatorLiveState {
        val json = JSONObject(body)
        val observedAt = json.getLong("observed_at_epoch_seconds")
        val staleAfter = json.optLong("stale_after_seconds", 60)
        val stale = json.optBoolean("stale", false) || nowSeconds() - observedAt > staleAfter
        val sectionsJson = json.optJSONArray("sections")
        val sections = buildList {
            if (sectionsJson != null) {
                for (index in 0 until sectionsJson.length()) {
                    val section = sectionsJson.getJSONObject(index)
                    add(
                        OperatorDashboardSection(
                            title = section.getString("title_uk"),
                            value = section.getString("value_uk"),
                            status = section.optString("status", "UNKNOWN"),
                            detail = section.optString("detail_uk").takeIf { it.isNotBlank() },
                        ),
                    )
                }
            }
        }

        return OperatorLiveState(
            status = if (stale) OperatorLiveStateStatus.STALE else OperatorLiveStateStatus.CURRENT,
            observedAtEpochSeconds = observedAt,
            checkedAtEpochSeconds = nowSeconds(),
            staleAfterSeconds = staleAfter,
            sections = sections,
            offlineReason = null,
        )
    }

    private fun nowSeconds(): Long = clock.instant().epochSecond
}
