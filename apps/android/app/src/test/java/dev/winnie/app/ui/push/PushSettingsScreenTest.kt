package dev.winnie.app.ui.push

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.core.app.ApplicationProvider
import dev.winnie.app.WinnieApplication
import dev.winnie.app.ui.theme.WinnieTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.GraphicsMode

@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class PushSettingsScreenTest {

    @get:Rule
    val compose = createComposeRule()

    @Test
    fun explainsTheTradeoffAndShowsTheSwitch() {
        val app = ApplicationProvider.getApplicationContext<WinnieApplication>()

        compose.setContent {
            WinnieTheme {
                PushSettingsScreen(container = app.container, onBack = {})
            }
        }

        compose.onNodeWithText("Alerts & background").assertIsDisplayed()
        compose.onNodeWithText("How this works").assertIsDisplayed()
        compose.onNodeWithText("Background connection").assertIsDisplayed()
        compose.onNodeWithText("Send test notification").assertExists()
    }
}
