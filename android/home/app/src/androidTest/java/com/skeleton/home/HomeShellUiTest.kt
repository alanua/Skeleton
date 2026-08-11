package com.skeleton.home

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertDoesNotExist
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import com.skeleton.home.auth.SyntheticSession
import com.skeleton.home.ui.HomeApp
import org.junit.Rule
import org.junit.Test

class HomeShellUiTest {
    @get:Rule
    val compose = createComposeRule()

    @Test
    fun operatorSeesPrimaryTabsAndOperatorHubEntry() {
        compose.setContent {
            HomeApp(session = SyntheticSession.operator())
        }

        compose.onNodeWithText("Головна").assertIsDisplayed()
        compose.onNodeWithText("Відео").assertIsDisplayed()
        compose.onNodeWithText("Пристрої").assertIsDisplayed()
        compose.onNodeWithContentDescription("operator-hub-entry").assertIsDisplayed()
    }

    @Test
    fun ordinarySessionDoesNotRenderOperatorHubEntry() {
        compose.setContent {
            HomeApp(session = SyntheticSession.ordinary())
        }

        compose.onNodeWithText("Головна").assertIsDisplayed()
        compose.onNodeWithText("СК").assertDoesNotExist()
    }
}
