package dev.winnie.app.ui.phone

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

/**
 * Renders the real consent screen against the real container. The build
 * machine has no emulator, so this catches a broken layout or a Compose wiring
 * mistake that a successful APK build would not.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class PhoneDataScreenTest {

    @get:Rule
    val compose = createComposeRule()

    @Test
    fun showsConsentAndScopes() {
        val app = ApplicationProvider.getApplicationContext<WinnieApplication>()

        compose.setContent {
            WinnieTheme {
                PhoneDataScreen(container = app.container, bottomBar = {})
            }
        }

        compose.onNodeWithText("Phone data").assertIsDisplayed()
        compose.onNodeWithText("Read this first").assertIsDisplayed()
        compose.onNodeWithText("Sync").assertIsDisplayed()
    }
}
