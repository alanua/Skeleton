package com.skeleton.home.data

import com.skeleton.home.domain.CanonicalHomeApi
import com.skeleton.home.domain.ConnectivityMonitor
import com.skeleton.home.domain.ConnectivityStatus
import com.skeleton.home.domain.HomeControlCapability
import com.skeleton.home.domain.HomeControlSurfaceState
import com.skeleton.home.domain.HomeMode
import com.skeleton.home.domain.HomeModeContext
import com.skeleton.home.domain.HomePlaceholderState
import com.skeleton.home.domain.HomeSession
import com.skeleton.home.domain.SyntheticActiveMedia
import com.skeleton.home.domain.SyntheticPlaybackState
import com.skeleton.home.domain.UserRole
import com.skeleton.home.domain.VerifiedActionState
import com.skeleton.home.domain.VerifiedActionStateStore

class SyntheticHomeRepository(
    private val connectivityMonitor: ConnectivityMonitor = StaticConnectivityMonitor(),
    private val actionStateStore: VerifiedActionStateStore = InMemoryVerifiedActionStateStore(),
) : CanonicalHomeApi {
    override suspend fun loadPlaceholderState(session: HomeSession): HomePlaceholderState =
        placeholderState(session)

    fun placeholderState(session: HomeSession): HomePlaceholderState =
        HomePlaceholderState(
            connectivityStatus = connectivityMonitor.currentStatus(),
            actionState = actionStateStore.currentActionState(),
            summary = "Синтетичний режим: канонічне джерело Home ще не підключене.",
            surface = syntheticSurfaceFor(session),
        )

    private fun syntheticSurfaceFor(session: HomeSession): HomeControlSurfaceState {
        val gamesAvailable = session.role != UserRole.ORDINARY
        return HomeControlSurfaceState(
            modes = listOf(
                HomeModeContext(HomeMode.YOUTUBE, "YouTube", isActive = true, isAvailable = true),
                HomeModeContext(HomeMode.CAST, "Cast", isActive = false, isAvailable = true),
                HomeModeContext(HomeMode.TV, "TV", isActive = false, isAvailable = true),
                HomeModeContext(HomeMode.GAMES, "Games", isActive = false, isAvailable = gamesAvailable),
            ),
            activeMedia = SyntheticActiveMedia(
                title = "Placeholder Series",
                year = "2026",
                seasonEpisodeLine = "Season 2 · Episode 4",
                artworkLabel = "Synthetic artwork",
            ),
            playback = SyntheticPlaybackState(
                positionLabel = "12:08",
                durationLabel = "43:20",
                progress = 0.28f,
                isPlaying = true,
                isMuted = false,
                volume = 0.62f,
            ),
            capabilities = setOf(
                HomeControlCapability.SEEK_BACK_15,
                HomeControlCapability.PLAY_PAUSE,
                HomeControlCapability.SEEK_FORWARD_15,
                HomeControlCapability.MUTE,
                HomeControlCapability.VOLUME,
            ),
        )
    }
}

class StaticConnectivityMonitor(
    private val status: ConnectivityStatus = ConnectivityStatus.OFFLINE,
) : ConnectivityMonitor {
    override fun currentStatus(): ConnectivityStatus = status
}

class InMemoryVerifiedActionStateStore : VerifiedActionStateStore {
    private var state: VerifiedActionState? = null

    override fun currentActionState(): VerifiedActionState? = state

    override fun recordActionState(state: VerifiedActionState) {
        this.state = state
    }
}
