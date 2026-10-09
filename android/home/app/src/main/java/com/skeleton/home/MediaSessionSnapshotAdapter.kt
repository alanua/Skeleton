package com.skeleton.home

import android.content.ComponentName
import android.content.Context
import android.media.MediaMetadata
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.provider.Settings
import org.json.JSONObject

enum class MediaSessionSnapshotAvailability {
    AVAILABLE,
    LISTENER_DISABLED,
    UNAVAILABLE,
}

enum class MediaSessionPlaybackStatus {
    PLAYING,
    PAUSED,
    IDLE,
}

data class MediaSessionSnapshot(
    val collectedAtMillis: Long,
    val status: MediaSessionPlaybackStatus,
    val positionMillis: Long?,
    val durationMillis: Long?,
    val title: String?,
)

data class MediaSessionSnapshotResult(
    val availability: MediaSessionSnapshotAvailability,
    val snapshot: MediaSessionSnapshot?,
)

class MediaSessionSnapshotAdapter(
    private val context: Context,
    private val allowedMediaPackages: Set<String> = emptySet(),
    private val nowMillis: () -> Long = { System.currentTimeMillis() },
) {
    companion object {
        private const val MAX_TITLE_LENGTH = 120

        fun isObservationListenerEnabled(context: Context): Boolean {
            val expected = ComponentName(context, MediaSessionObservationListener::class.java)
            val enabledListeners = Settings.Secure.getString(
                context.contentResolver,
                Settings.Secure.ENABLED_NOTIFICATION_LISTENERS,
            ) ?: return false

            return enabledListeners
                .split(':')
                .mapNotNull { ComponentName.unflattenFromString(it) }
                .any { it.packageName == expected.packageName && it.className == expected.className }
        }
    }

    fun readSnapshot(): MediaSessionSnapshotResult {
        if (allowedMediaPackages.isEmpty()) {
            return MediaSessionSnapshotResult(
                availability = MediaSessionSnapshotAvailability.UNAVAILABLE,
                snapshot = null,
            )
        }
        if (!isObservationListenerEnabled(context)) {
            return MediaSessionSnapshotResult(
                availability = MediaSessionSnapshotAvailability.LISTENER_DISABLED,
                snapshot = null,
            )
        }

        val manager = context.getSystemService(MediaSessionManager::class.java)
            ?: return MediaSessionSnapshotResult(
                availability = MediaSessionSnapshotAvailability.UNAVAILABLE,
                snapshot = null,
            )

        val controllers = try {
            manager.getActiveSessions(ComponentName(context, MediaSessionObservationListener::class.java))
        } catch (_: SecurityException) {
            return MediaSessionSnapshotResult(
                availability = MediaSessionSnapshotAvailability.LISTENER_DISABLED,
                snapshot = null,
            )
        }

        val allowedControllers = controllers
            .asSequence()
            .filter { it.packageName in allowedMediaPackages }
            .toList()

        val controller = allowedControllers
            .sortedWith(
                compareByDescending<MediaController> { it.playbackState?.isActivelyPlaying() == true }
                    .thenBy { it.packageName }
            )
            .firstOrNull()

        val snapshot = controller?.toSnapshot(nowMillis())
            ?: MediaSessionSnapshot(
                collectedAtMillis = nowMillis(),
                status = MediaSessionPlaybackStatus.IDLE,
                positionMillis = null,
                durationMillis = null,
                title = null,
            )

        return MediaSessionSnapshotResult(
            availability = MediaSessionSnapshotAvailability.AVAILABLE,
            snapshot = snapshot,
        )
    }

    private fun MediaController.toSnapshot(collectedAtMillis: Long): MediaSessionSnapshot {
        val state = playbackState
        val metadata = metadata

        return MediaSessionSnapshot(
            collectedAtMillis = collectedAtMillis,
            status = state.toPlaybackStatus(),
            positionMillis = state?.position?.takeUnless { it == PlaybackState.PLAYBACK_POSITION_UNKNOWN }?.coerceAtLeast(0),
            durationMillis = metadata?.getLong(MediaMetadata.METADATA_KEY_DURATION)?.takeIf { it > 0 },
            title = metadata?.getString(MediaMetadata.METADATA_KEY_TITLE).redactedTitle(),
        )
    }

    private fun PlaybackState?.toPlaybackStatus(): MediaSessionPlaybackStatus =
        when (this?.state) {
            PlaybackState.STATE_PLAYING,
            PlaybackState.STATE_BUFFERING,
            PlaybackState.STATE_FAST_FORWARDING,
            PlaybackState.STATE_REWINDING,
            PlaybackState.STATE_SKIPPING_TO_NEXT,
            PlaybackState.STATE_SKIPPING_TO_PREVIOUS,
            PlaybackState.STATE_SKIPPING_TO_QUEUE_ITEM,
            -> MediaSessionPlaybackStatus.PLAYING
            PlaybackState.STATE_PAUSED -> MediaSessionPlaybackStatus.PAUSED
            else -> MediaSessionPlaybackStatus.IDLE
        }

    private fun PlaybackState.isActivelyPlaying(): Boolean =
        toPlaybackStatus() == MediaSessionPlaybackStatus.PLAYING

    private fun String?.redactedTitle(): String? {
        val clean = this
            ?.filterNot { Character.isISOControl(it) }
            ?.trim()
            ?.take(MAX_TITLE_LENGTH)
        return clean?.takeIf { it.isNotBlank() }
    }
}

fun MediaSessionSnapshotResult.toSkeletonJson(): JSONObject =
    JSONObject()
        .put("schema", "skeleton.android.media_session_snapshot.v1")
        .put("availability", availability.name.lowercase())
        .put(
            "snapshot",
            snapshot?.let {
                JSONObject()
                    .put("collected_at_millis", it.collectedAtMillis)
                    .put("status", it.status.name.lowercase())
                    .put("position_millis", it.positionMillis ?: JSONObject.NULL)
                    .put("duration_millis", it.durationMillis ?: JSONObject.NULL)
                    .put("title", it.title ?: JSONObject.NULL)
            } ?: JSONObject.NULL,
        )
