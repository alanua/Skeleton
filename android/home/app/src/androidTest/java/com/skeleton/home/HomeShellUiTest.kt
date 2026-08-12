package com.skeleton.home

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertDoesNotExist
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
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
        compose.onNodeWithText("\u041f\u0443\u043b\u044c\u0442").assertDoesNotExist()
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
        compose.onNodeWithText("\u041f\u0443\u043b\u044c\u0442").assertDoesNotExist()
    }

    @Test
    fun homeScreenShowsCurrentSyntheticVisualStructure() {
        compose.setContent {
            HomeApp(session = SyntheticSession.operator())
        }

        compose.onNodeWithContentDescription("home-top-status-row").assertIsDisplayed()
        compose.onNodeWithContentDescription("home-mode-row").assertIsDisplayed()
        compose.onNodeWithText("YouTube").assertIsDisplayed()
        compose.onNodeWithText("Cast").assertIsDisplayed()
        compose.onNodeWithText("TV").assertIsDisplayed()
        compose.onNodeWithText("Games").assertIsDisplayed()
        compose.onNodeWithContentDescription("home-active-media-card").assertIsDisplayed()
        compose.onNodeWithContentDescription("home-artwork-placeholder").assertIsDisplayed()
        compose.onNodeWithText("Placeholder Series").assertIsDisplayed()
        compose.onNodeWithText("Season 2 · Episode 4").assertIsDisplayed()
        compose.onNodeWithContentDescription("home-playback-progress").assertIsDisplayed()
        compose.onNodeWithContentDescription("home-adaptive-controls").assertIsDisplayed()
        compose.onNodeWithContentDescription("control-back-15").assertIsDisplayed()
        compose.onNodeWithContentDescription("control-play-pause").assertIsDisplayed()
        compose.onNodeWithContentDescription("control-forward-15").assertIsDisplayed()
        compose.onNodeWithContentDescription("control-mute").assertIsDisplayed()
        compose.onNodeWithContentDescription("home-volume-control").assertIsDisplayed()
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
}
