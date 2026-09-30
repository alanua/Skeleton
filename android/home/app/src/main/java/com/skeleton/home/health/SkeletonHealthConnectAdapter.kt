package com.skeleton.home.health

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import java.time.Duration
import java.time.Instant
import org.json.JSONArray
import org.json.JSONObject

data class HealthStepsSummary(
    val windowStart: Instant,
    val windowEnd: Instant,
    val totalSteps: Long,
    val recordCount: Int,
)

data class HealthSleepStageSummary(
    val stage: String,
    val durationSeconds: Long,
)

data class HealthSleepSessionSummary(
    val start: Instant,
    val end: Instant,
    val durationSeconds: Long,
    val stages: List<HealthSleepStageSummary>,
)

data class HealthConnectSummary(
    val collectedAt: Instant,
    val steps: HealthStepsSummary?,
    val sleepSessions: List<HealthSleepSessionSummary>,
)

enum class HealthConnectAvailability {
    AVAILABLE,
    UPDATE_REQUIRED,
    UNAVAILABLE,
}

class SkeletonHealthConnectAdapter(
    private val context: Context,
    private val now: () -> Instant = { Instant.now() },
) {
    companion object {
        val readPermissions: Set<String> = setOf(
            HealthPermission.getReadPermission(StepsRecord::class),
            HealthPermission.getReadPermission(SleepSessionRecord::class),
        )

        fun availability(context: Context): HealthConnectAvailability =
            when (HealthConnectClient.getSdkStatus(context)) {
                HealthConnectClient.SDK_AVAILABLE -> HealthConnectAvailability.AVAILABLE
                HealthConnectClient.SDK_UNAVAILABLE_PROVIDER_UPDATE_REQUIRED ->
                    HealthConnectAvailability.UPDATE_REQUIRED
                else -> HealthConnectAvailability.UNAVAILABLE
            }

        fun permissionContract() = PermissionController.createRequestPermissionResultContract()
    }

    private fun client(): HealthConnectClient = HealthConnectClient.getOrCreate(context)

    suspend fun grantedPermissions(): Set<String> {
        if (availability(context) != HealthConnectAvailability.AVAILABLE) return emptySet()
        return client().permissionController.getGrantedPermissions()
    }

    suspend fun hasReadPermissions(): Boolean =
        grantedPermissions().containsAll(readPermissions)

    suspend fun readRecentSummary(): HealthConnectSummary? {
        if (availability(context) != HealthConnectAvailability.AVAILABLE) return null
        val client = client()
        if (!client.permissionController.getGrantedPermissions().containsAll(readPermissions)) return null

        val collectedAt = now()
        val stepsStart = collectedAt.minus(Duration.ofHours(24))
        val sleepStart = collectedAt.minus(Duration.ofHours(36))

        val stepRecords = client.readRecords(
            ReadRecordsRequest(
                recordType = StepsRecord::class,
                timeRangeFilter = TimeRangeFilter.between(stepsStart, collectedAt),
            )
        ).records

        val sleepRecords = client.readRecords(
            ReadRecordsRequest(
                recordType = SleepSessionRecord::class,
                timeRangeFilter = TimeRangeFilter.between(sleepStart, collectedAt),
            )
        ).records

        return HealthConnectSummary(
            collectedAt = collectedAt,
            steps = HealthStepsSummary(
                windowStart = stepsStart,
                windowEnd = collectedAt,
                totalSteps = stepRecords.sumOf { it.count },
                recordCount = stepRecords.size,
            ),
            sleepSessions = sleepRecords
                .sortedBy { it.startTime }
                .takeLast(16)
                .map { record ->
                    HealthSleepSessionSummary(
                        start = record.startTime,
                        end = record.endTime,
                        durationSeconds = Duration.between(record.startTime, record.endTime).seconds.coerceAtLeast(0),
                        stages = summarizeStages(record),
                    )
                },
        )
    }

    private fun summarizeStages(record: SleepSessionRecord): List<HealthSleepStageSummary> =
        record.stages
            .groupBy { stageName(it.stage) }
            .map { (name, stages) ->
                HealthSleepStageSummary(
                    stage = name,
                    durationSeconds = stages.sumOf {
                        Duration.between(it.startTime, it.endTime).seconds.coerceAtLeast(0)
                    },
                )
            }
            .sortedBy { it.stage }

    private fun stageName(stage: Int): String =
        when (stage) {
            SleepSessionRecord.STAGE_TYPE_AWAKE -> "awake"
            SleepSessionRecord.STAGE_TYPE_AWAKE_IN_BED -> "awake_in_bed"
            SleepSessionRecord.STAGE_TYPE_DEEP -> "deep"
            SleepSessionRecord.STAGE_TYPE_LIGHT -> "light"
            SleepSessionRecord.STAGE_TYPE_OUT_OF_BED -> "out_of_bed"
            SleepSessionRecord.STAGE_TYPE_REM -> "rem"
            SleepSessionRecord.STAGE_TYPE_SLEEPING -> "sleeping"
            SleepSessionRecord.STAGE_TYPE_UNKNOWN -> "unknown"
            else -> "other"
        }
}

fun HealthConnectSummary.toSkeletonJson(): JSONObject =
    JSONObject()
        .put("schema", "skeleton.android.health_summary.v1")
        .put("collected_at", collectedAt.toString())
        .put(
            "steps",
            steps?.let {
                JSONObject()
                    .put("window_start", it.windowStart.toString())
                    .put("window_end", it.windowEnd.toString())
                    .put("total_steps", it.totalSteps)
                    .put("record_count", it.recordCount)
            } ?: JSONObject.NULL,
        )
        .put(
            "sleep_sessions",
            JSONArray().apply {
                sleepSessions.forEach { session ->
                    put(
                        JSONObject()
                            .put("start", session.start.toString())
                            .put("end", session.end.toString())
                            .put("duration_seconds", session.durationSeconds)
                            .put(
                                "stages",
                                JSONArray().apply {
                                    session.stages.forEach { stage ->
                                        put(
                                            JSONObject()
                                                .put("stage", stage.stage)
                                                .put("duration_seconds", stage.durationSeconds)
                                        )
                                    }
                                },
                            )
                    )
                }
            },
        )

