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
}
