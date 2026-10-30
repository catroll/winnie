package dev.winnie.app.ui.login

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import dev.winnie.app.data.DEFAULT_SERVER_URL
import dev.winnie.app.ui.theme.WinnieTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.GraphicsMode

/**
 * Renders the real login composable in a Robolectric Android runtime. There is
 * no emulator on the build machine, so this is the UI smoke test: it catches a
 * broken theme, an unreadable layout resource or a Compose wiring mistake that
 * a successful APK build would not.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class LoginScreenTest {

    @get:Rule
    val compose = createComposeRule()

    @Test
    fun showsFormAndSwitchesToRegistration() {
        var state by mutableStateOf(LoginUiState(serverUrl = DEFAULT_SERVER_URL))
        var submitted: Pair<String, String>? = null

        compose.setContent {
            WinnieTheme {
                LoginScreen(
                    ui = state,
                    notice = null,
                    onServerUrlChange = { state = state.copy(serverUrl = it) },
                    onRegisterModeChange = { state = state.copy(registerMode = it) },
                    onSubmit = { username, password -> submitted = username to password },
                )
            }
        }

        compose.onNodeWithText("Winnie").assertIsDisplayed()
        compose.onNodeWithText("Sign in").assertIsDisplayed()

        compose.onNodeWithText("Create the first account")
            .performScrollTo()
            .performClick()
        compose.onNodeWithText("Create account").assertExists()

        compose.onNodeWithTag("username").performScrollTo().performTextInput("alice")
        compose.onNodeWithTag("password").performScrollTo().performTextInput("correct horse")
        compose.onNodeWithTag("submit").performScrollTo().performClick()

        assert(submitted == ("alice" to "correct horse")) {
            "submit callback got $submitted"
        }
    }

    @Test
    fun showsServerErrorAndNotice() {
        compose.setContent {
            WinnieTheme {
                LoginScreen(
                    ui = LoginUiState(serverUrl = DEFAULT_SERVER_URL),
                    notice = "Your session expired. Sign in again.",
                    onServerUrlChange = {},
                    onRegisterModeChange = {},
                    onSubmit = { _, _ -> },
                )
            }
        }

        compose.onNodeWithText("Your session expired. Sign in again.")
            .assertExists()
    }
}
