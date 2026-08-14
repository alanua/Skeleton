package com.skeleton.home

import com.skeleton.home.auth.SyntheticSession
import com.skeleton.home.data.freshnessLabel
import com.skeleton.home.data.parseOperatorDashboard
import com.skeleton.home.domain.ConnectivityStatus
import com.skeleton.home.domain.OperatorDashboardFreshness
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
    fun operatorDashboardParsesSuccessiveLiveSnapshotsWithoutRebuild() {
        val first = parseOperatorDashboard(
            """
            {
              "schema": "skeleton.operator_live_state.v1",
              "source_channel": "core.operator_overview.load_operator_overview",
              "refreshed_at": "2026-08-14T10:00:00Z",
              "freshness": "current",
              "sections": [
                {"title_uk": "Працює зараз", "empty_uk": "Немає", "rows": ["Перевіряє поточний запуск."]},
                {"title_uk": "Будується зараз", "empty_uk": "Немає", "rows": ["Збирає перший зріз."]}
              ]
            }
            """.trimIndent(),
        )
        val second = parseOperatorDashboard(
            """
            {
              "schema": "skeleton.operator_live_state.v1",
              "source_channel": "core.operator_overview.load_operator_overview",
              "refreshed_at": "2026-08-14T10:01:00Z",
              "freshness": "current",
              "sections": [
                {"title_uk": "Працює зараз", "empty_uk": "Немає", "rows": ["Перевіряє наступний запуск."]},
                {"title_uk": "Будується зараз", "empty_uk": "Немає", "rows": ["Збирає live-екран."]}
              ]
            }
            """.trimIndent(),
        )

        assertEquals(OperatorDashboardFreshness.CURRENT, first.freshness)
        assertEquals("Оновлено щойно", freshnessLabel(first))
        assertFalse(first.sections[0].rows == second.sections[0].rows)
        assertFalse(first.sections[1].rows == second.sections[1].rows)
    }

    @Test
    fun staleOperatorDashboardNeverRendersUpdatedJustNow() {
        val stale = parseOperatorDashboard(
            """
            {
              "schema": "skeleton.operator_live_state.v1",
              "source_channel": "core.operator_overview.load_operator_overview",
              "refreshed_at": "2026-08-14T09:30:00Z",
              "freshness": "stale",
              "sections": []
            }
            """.trimIndent(),
        )

        assertEquals(OperatorDashboardFreshness.STALE, stale.freshness)
        assertFalse(freshnessLabel(stale).contains("Оновлено щойно"))
    }

    @Test
    fun primaryOperatorDashboardRowsContainNoInternalNumbersOrRunnerLabels() {
        val state = parseOperatorDashboard(
            """
            {
              "schema": "skeleton.operator_live_state.v1",
              "source_channel": "core.operator_overview.load_operator_overview",
              "refreshed_at": "2026-08-14T10:00:00Z",
              "freshness": "current",
              "sections": [
                {"title_uk": "Працює зараз", "empty_uk": "Немає", "rows": ["Черга Runner: технічне посилання і технічна мітка рухаються."]}
              ]
            }
            """.trimIndent(),
        )

        val primary = state.sections.flatMap { it.rows }.joinToString(" ")
        assertFalse(Regex("""(?i)(issue|pr|task)\s*#?\d+|#\d+|runner/[A-Za-z0-9._/-]+""").containsMatchIn(primary))
    }
}
