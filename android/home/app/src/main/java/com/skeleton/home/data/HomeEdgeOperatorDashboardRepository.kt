package com.skeleton.home.data

import com.skeleton.home.domain.ConnectivityStatus
import com.skeleton.home.domain.HomeSession
import com.skeleton.home.domain.OperatorDashboardRepository
import com.skeleton.home.domain.OperatorDashboardSections
import com.skeleton.home.domain.OperatorDashboardState
import com.skeleton.home.domain.OperatorLiveItem
import java.net.HttpURLConnection
import java.net.URL
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

class HomeEdgeOperatorDashboardRepository(
    private val endpointConfig: HomeEdgeEndpointConfig = HomeEdgeEndpointConfig(),
) : OperatorDashboardRepository {
    override suspend fun loadDashboard(session: HomeSession): OperatorDashboardState =
        withContext(Dispatchers.IO) {
            try {
                val connection = URL(endpointConfig.operatorLiveStateUrl()).openConnection() as HttpURLConnection
                connection.requestMethod = "GET"
                connection.connectTimeout = 2_000
                connection.readTimeout = 3_000
                connection.setRequestProperty("Accept", "application/json")
                val statusCode = connection.responseCode
                val body = if (statusCode in 200..299) {
                    connection.inputStream.bufferedReader().use { it.readText() }
                } else {
                    connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                }
                if (statusCode !in 200..299) {
                    return@withContext offlineDashboard()
                }
                parseDashboard(JSONObject(body))
            } catch (_: Exception) {
                offlineDashboard()
            }
        }

    private fun parseDashboard(root: JSONObject): OperatorDashboardState {
        val sections = root.optJSONObject("sections") ?: JSONObject()
        val stale = root.optBoolean("stale", true)
        return OperatorDashboardState(
            connectivityStatus = when {
                root.optString("status") == "online" && !stale -> ConnectivityStatus.ONLINE
                root.optString("status") == "stale" -> ConnectivityStatus.DEGRADED
                else -> ConnectivityStatus.OFFLINE
            },
            stale = stale,
            refreshedAt = root.optLongOrNull("refreshed_at"),
            message = root.optString("message", "Стан тимчасово недоступний."),
            sections = OperatorDashboardSections(
                workingNow = sections.items("Працює зараз"),
                waiting = sections.items("Чекає"),
                needsAttention = sections.items("Потрібна моя увага"),
                recentlyDone = sections.items("Щойно завершено"),
                next = sections.items("Далі"),
            ),
        )
    }

    private fun JSONObject.items(name: String): List<OperatorLiveItem> {
        val array = optJSONArray(name) ?: JSONArray()
        return buildList {
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                add(
                    OperatorLiveItem(
                        title = item.optString("title", "Завдання"),
                        detail = item.optString("detail", ""),
                        updatedAt = item.optLong("updated_at", 0L),
                    ),
                )
            }
        }
    }

    private fun JSONObject.optLongOrNull(name: String): Long? =
        if (has(name) && !isNull(name)) optLong(name) else null
}

fun offlineDashboard(): OperatorDashboardState =
    OperatorDashboardState(
        connectivityStatus = ConnectivityStatus.OFFLINE,
        stale = true,
        refreshedAt = null,
        message = "Живий стан тимчасово недоступний.",
    )
