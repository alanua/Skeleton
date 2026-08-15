package com.skeleton.home

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertDoesNotExist
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.skeleton.home.auth.SyntheticSession
import com.skeleton.home.navigation.HomeRoute
import com.skeleton.home.ui.HomeApp
import org.junit.Rule
import org.junit.Test

class HomeShellUiTest {
    @get:Rule
    val compose = createComposeRule()

    @Test
    fun operatorSeesFourBottomTabs() {
        compose.setContent {
            HomeApp(session = SyntheticSession.operator())
        }

        compose.onNodeWithContentDescription("bottom-nav-home").assertIsDisplayed()
        compose.onNodeWithContentDescription("bottom-nav-video").assertIsDisplayed()
        compose.onNodeWithContentDescription("bottom-nav-devices").assertIsDisplayed()
        compose.onNodeWithContentDescription("bottom-nav-operator-hub").assertIsDisplayed()
    }

    @Test
    fun ordinarySessionSeesThreeBottomTabs() {
        compose.setContent {
            HomeApp(session = SyntheticSession.ordinary())
        }

        compose.onNodeWithContentDescription("bottom-nav-home").assertIsDisplayed()
        compose.onNodeWithContentDescription("bottom-nav-video").assertIsDisplayed()
        compose.onNodeWithContentDescription("bottom-nav-devices").assertIsDisplayed()
        compose.onNodeWithContentDescription("bottom-nav-operator-hub").assertDoesNotExist()
    }

    @Test
    fun spouseDirectOperatorHubRouteIsDenied() {
        compose.setContent {
            HomeApp(
                session = SyntheticSession.spouse(),
                initialRoute = HomeRoute.OperatorHub,
            )
        }

        compose.onNodeWithText("Доступ до розділу відхилено для цього синтетичного профілю.")
            .assertIsDisplayed()
        compose.onNodeWithContentDescription("bottom-nav-operator-hub").assertDoesNotExist()
    }

    @Test
    fun videoSearchRetryActionIsVisibleAndClickable() {
        compose.setContent {
            HomeApp(
                session = SyntheticSession.ordinary(),
                initialRoute = HomeRoute.Video,
            )
        }

        compose.onNodeWithText("Відео").assertIsDisplayed()
        compose.onNodeWithContentDescription("media-search-retry").assertIsDisplayed()
        compose.onNodeWithContentDescription("media-search-retry").performClick()
        compose.onNodeWithText("Пошук релізу").assertIsDisplayed()
    }

    @Test
    fun videoControlsExposeDifferentSeriesSeasonAndEpisodeActions() {
        compose.setContent {
            HomeApp(
                session = SyntheticSession.ordinary(),
                initialRoute = HomeRoute.Video,
            )
        }

        compose.onNodeWithContentDescription("media-series-season-control").assertIsDisplayed()
        compose.onNodeWithContentDescription("media-episode-control").assertIsDisplayed()
        compose.onNodeWithContentDescription("work-description-auto-scroll").assertIsDisplayed()
    }
}
