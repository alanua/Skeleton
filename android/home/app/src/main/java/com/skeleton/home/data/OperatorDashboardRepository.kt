package com.skeleton.home.data

import com.skeleton.home.domain.HomeSession
import com.skeleton.home.domain.OperatorDashboardApi
import com.skeleton.home.domain.OperatorDashboardFreshness
import com.skeleton.home.domain.OperatorDashboardSection
import com.skeleton.home.domain.OperatorDashboardState
import com.skeleton.home.domain.UserRole
import java.net.HttpURLConnection
import java.net.URL
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

class HomeEdgeOperatorDashboardRepository(
    private val endpoint: String = System.getProperty("skeleton.operator.live_state_url").orEmpty(),
) : OperatorDashboardApi {
    override suspend fun loadOperatorDashboard(session: HomeSession): OperatorDashboardState {
        if (session.role != UserRole.OPERATOR) {
            return unavailableOperatorDashboard("operator_access_denied")
        }
        if (endpoint.isBlank()) {
            return unavailableOperatorDashboard("live_state_endpoint_missing")
        }
        return withContext(Dispatchers.IO) {
            try {
                val connection = URL(endpoint).openConnection() as HttpURLConnection
                connection.requestMethod = "GET"
                connection.connectTimeout = 2500
                connection.readTimeout = 2500
                connection.setRequestProperty("Accept", "application/json")
                val code = connection.responseCode
                val body = if (code in 200..299) {
                    connection.inputStream.bufferedReader().use { it.readText() }
                } else {
                    connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                }
                if (code in 200..299) {
                    parseOperatorDashboard(body)
                } else {
                    unavailableOperatorDashboard("live_state_http_$code", body.take(160))
                }
            } catch (exception: Exception) {
                unavailableOperatorDashboard("live_state_unavailable", exception.message)
            }
        }
    }
}

fun parseOperatorDashboard(rawJson: String): OperatorDashboardState {
    val root = JSONObject(rawJson)
    val schema = root.optString("schema")
    if (schema != "skeleton.operator_live_state.v1") {
        return unavailableOperatorDashboard("invalid_live_state_schema")
    }
    val sections = root.optJSONArray("sections")
    val parsedSections = buildList {
        if (sections != null) {
            for (index in 0 until sections.length()) {
                val section = sections.optJSONObject(index) ?: continue
                val rows = section.optJSONArray("rows")
                add(
                    OperatorDashboardSection(
                        titleUk = section.optString("title_uk"),
                        emptyUk = section.optString("empty_uk"),
                        rows = buildList {
                            if (rows != null) {
                                for (rowIndex in 0 until rows.length()) {
                                    val row = rows.optString(rowIndex).trim()
                                    if (row.isNotEmpty()) {
                                        add(row)
                                    }
                                }
                            }
                        },
                    ),
                )
            }
        }
    }
    return OperatorDashboardState(
        sourceChannel = root.optString("source_channel"),
        refreshedAt = root.optString("refreshed_at").takeIf { it.isNotBlank() },
        freshness = freshnessFromWire(root.optString("freshness")),
        sections = parsedSections,
        error = root.optString("error").takeIf { it.isNotBlank() },
    )
}

fun unavailableOperatorDashboard(
    reason: String,
    detail: String? = null,
): OperatorDashboardState =
    OperatorDashboardState(
        sourceChannel = "core.operator_overview.load_operator_overview",
        refreshedAt = null,
        freshness = OperatorDashboardFreshness.OFFLINE,
        sections = emptyList(),
        error = listOf(reason, detail).filter { !it.isNullOrBlank() }.joinToString(": "),
    )

fun freshnessLabel(state: OperatorDashboardState): String =
    when (state.freshness) {
        OperatorDashboardFreshness.CURRENT -> "Оновлено щойно"
        OperatorDashboardFreshness.STALE -> "Дані застаріли: ${state.refreshedAt ?: "час невідомий"}"
        OperatorDashboardFreshness.OFFLINE -> "Немає живого оновлення"
        OperatorDashboardFreshness.DEGRADED -> "Оновлення неповне: ${state.refreshedAt ?: "час невідомий"}"
    }

private fun freshnessFromWire(value: String): OperatorDashboardFreshness =
    when (value.lowercase()) {
        "current" -> OperatorDashboardFreshness.CURRENT
        "stale" -> OperatorDashboardFreshness.STALE
        "degraded", "partial" -> OperatorDashboardFreshness.DEGRADED
        else -> OperatorDashboardFreshness.OFFLINE
    }
