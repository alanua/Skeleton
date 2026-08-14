package com.skeleton.home

import com.skeleton.home.auth.SyntheticSession
import com.skeleton.home.domain.ConnectivityStatus
import com.skeleton.home.domain.OperatorDashboardSections
import com.skeleton.home.domain.OperatorDashboardState
import com.skeleton.home.domain.OperatorLiveItem
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
    fun operatorDashboardSectionsUseSimpleUkrainianPrimaryLabels() {
        val state = OperatorDashboardState(
            connectivityStatus = ConnectivityStatus.ONLINE,
            stale = false,
            refreshedAt = 10L,
            message = "Стан оновлено",
            sections = OperatorDashboardSections(
                workingNow = listOf(OperatorLiveItem("Працює", "Деталі", 10L)),
                waiting = listOf(OperatorLiveItem("Чекає", "Деталі", 9L)),
                needsAttention = listOf(OperatorLiveItem("Потрібна дія", "Деталі", 8L)),
                recentlyDone = listOf(OperatorLiveItem("Готово", "Деталі", 7L)),
                next = listOf(OperatorLiveItem("Далі", "Деталі", 6L)),
            ),
        )

        assertEquals("Працює", state.sections.workingNow.single().title)
        assertEquals("Чекає", state.sections.waiting.single().title)
        assertEquals("Потрібна дія", state.sections.needsAttention.single().title)
        assertEquals("Готово", state.sections.recentlyDone.single().title)
        assertEquals("Далі", state.sections.next.single().title)
        assertTrue(state.sections.workingNow.single().drillDown.isEmpty())
    }
}
