package com.skeleton.home

import com.skeleton.home.auth.SyntheticSession
import com.skeleton.home.data.SyntheticHomeRepository
import com.skeleton.home.domain.HomeControlCapability
import com.skeleton.home.domain.ConnectivityStatus
import com.skeleton.home.domain.UserRole
import com.skeleton.home.domain.VerifiedActionState
import com.skeleton.home.navigation.HomeRoute
import com.skeleton.home.navigation.PrimaryBottomRoutes
import com.skeleton.home.navigation.bottomRoutesFor
import com.skeleton.home.navigation.canNavigateTo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HomeContractTest {
    @Test
    fun operatorBottomNavigationContainsExactlyFourCanonicalTabs() {
        val operator = SyntheticSession.operator()

        assertEquals(
            listOf("Головна", "Відео", "Пристрої", "СК"),
            bottomRoutesFor(operator.currentSession(), operator).map { it.label },
        )
        assertEquals(listOf("home", "video", "devices", "operator-hub"), bottomRoutesFor(operator.currentSession(), operator).map { it.route })
    }

    @Test
    fun ordinaryAndSpouseBottomNavigationContainsExactlyThreeCanonicalTabs() {
        val ordinary = SyntheticSession.ordinary()
        val spouse = SyntheticSession.spouse()

        assertEquals(listOf("Головна", "Відео", "Пристрої"), PrimaryBottomRoutes.map { it.label })
        assertEquals(
            listOf("Головна", "Відео", "Пристрої"),
            bottomRoutesFor(ordinary.currentSession(), ordinary).map { it.label },
        )
        assertEquals(
            listOf("Головна", "Відео", "Пристрої"),
            bottomRoutesFor(spouse.currentSession(), spouse).map { it.label },
        )
        assertFalse(bottomRoutesFor(ordinary.currentSession(), ordinary).contains(HomeRoute.OperatorHub))
        assertFalse(bottomRoutesFor(spouse.currentSession(), spouse).contains(HomeRoute.OperatorHub))
    }

    @Test
    fun directOperatorHubAuthorizationFailsClosedForNonOperators() {
        val ordinary = SyntheticSession.ordinary()
        val spouse = SyntheticSession.spouse()
        val operator = SyntheticSession.operator()

        assertTrue(canNavigateTo(HomeRoute.OperatorHub, operator.currentSession(), operator))
        assertFalse(canNavigateTo(HomeRoute.OperatorHub, ordinary.currentSession(), ordinary))
        assertFalse(canNavigateTo(HomeRoute.OperatorHub, spouse.currentSession(), spouse))
    }

    @Test
    fun roleAndStateEnumsPreserveFutureContractValues() {
        assertEquals(listOf("OPERATOR", "ORDINARY", "SPOUSE"), UserRole.entries.map { it.name })
        assertEquals(listOf("ONLINE", "DEGRADED", "OFFLINE"), ConnectivityStatus.entries.map { it.name })
        assertEquals(
            listOf("SENT", "ACCEPTED", "APPLIED", "PHYSICALLY_VERIFIED"),
            VerifiedActionState.entries.map { it.name },
        )
    }

    @Test
    fun syntheticHomeSurfaceProvidesCurrentControlStructureWithoutLiveApis() {
        val state = SyntheticHomeRepository().placeholderState(SyntheticSession.operator().currentSession())

        assertEquals(listOf("YouTube", "Cast", "TV", "Games"), state.surface.modes.map { it.label })
        assertEquals("Placeholder Series", state.surface.activeMedia.title)
        assertEquals("Season 2 · Episode 4", state.surface.activeMedia.seasonEpisodeLine)
        assertEquals(
            setOf(
                HomeControlCapability.SEEK_BACK_15,
                HomeControlCapability.PLAY_PAUSE,
                HomeControlCapability.SEEK_FORWARD_15,
                HomeControlCapability.MUTE,
                HomeControlCapability.VOLUME,
            ),
            state.surface.capabilities,
        )
    }
}
