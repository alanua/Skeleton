package com.skeleton.home

import com.skeleton.home.auth.SyntheticSession
import com.skeleton.home.domain.ConnectivityStatus
import com.skeleton.home.domain.UserRole
import com.skeleton.home.domain.VerifiedActionState
import com.skeleton.home.navigation.HomeRoute
import com.skeleton.home.navigation.OperatorBottomRoutes
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
        assertEquals(listOf(HomeRoute.Home, HomeRoute.Video, HomeRoute.Devices, HomeRoute.OperatorHub), OperatorBottomRoutes)
        assertFalse(bottomRoutesFor(operator.currentSession(), operator).contains(HomeRoute.Remote))
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
        assertFalse(PrimaryBottomRoutes.contains(HomeRoute.Remote))
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
    fun routeIdsRestoreStableDestinations() {
        assertEquals(HomeRoute.Video, HomeRoute.fromRoute("video"))
        assertEquals(HomeRoute.Devices, HomeRoute.fromRoute("devices"))
        assertEquals(HomeRoute.Home, HomeRoute.fromRoute("missing"))
    }
}
