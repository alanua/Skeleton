package com.skeleton.home

import com.skeleton.home.auth.SyntheticSession
import com.skeleton.home.domain.ConnectivityStatus
import com.skeleton.home.domain.UserRole
import com.skeleton.home.domain.VerifiedActionState
import com.skeleton.home.navigation.HomeRoute
import com.skeleton.home.navigation.PrimaryBottomRoutes
import com.skeleton.home.navigation.canNavigateTo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HomeContractTest {
    @Test
    fun bottomNavigationContainsExactlyPrimaryTabs() {
        assertEquals(listOf("Головна", "Відео", "Пристрої"), PrimaryBottomRoutes.map { it.label })
        assertFalse(PrimaryBottomRoutes.contains(HomeRoute.Remote))
    }

    @Test
    fun operatorHubAuthorizationFailsClosedForNonOperators() {
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
}
