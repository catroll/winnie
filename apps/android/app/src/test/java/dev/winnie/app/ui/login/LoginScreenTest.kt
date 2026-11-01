package dev.winnie.app.ui.login

import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import dev.winnie.app.data.DEFAULT_SERVER_URL
import dev.winnie.app.ui.theme.WinnieTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
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

    private val state = mutableStateOf(LoginUiState(serverUrl = DEFAULT_SERVER_URL))
    private var submissions = 0

    private fun render(notice: String? = null) {
        compose.setContent {
            WinnieTheme {
                LoginScreen(
                    ui = state.value,
                    notice = notice,
                    onServerUrlChange = { state.value = state.value.copy(serverUrl = it) },
                    onRegisterModeChange = {
                        state.value = state.value.copy(registerMode = it)
                    },
                    onUsernameChange = { state.value = state.value.copy(username = it) },
                    onPasswordChange = { state.value = state.value.copy(password = it) },
                    onRememberChange = { state.value = state.value.copy(remember = it) },
                    onSubmit = { submissions++ },
                )
            }
        }
    }

    @Test
    fun showsFormAndSwitchesToRegistration() {
        render()

        compose.onNodeWithText("Winnie").assertIsDisplayed()
        compose.onNodeWithText("Sign in").performScrollTo().assertIsDisplayed()

        compose.onNodeWithText("Create the first account")
            .performScrollTo()
            .performClick()
        compose.onNodeWithText("Create account").assertExists()

        compose.onNodeWithTag("username").performScrollTo().performTextInput("alice")
        compose.onNodeWithTag("password").performScrollTo().performTextInput("correct horse")
        compose.onNodeWithTag("submit").performScrollTo().performClick()

        assertEquals("alice", state.value.username)
        assertEquals("correct horse", state.value.password)
        assertEquals(1, submissions)
    }

    @Test
    fun prefillsTheRememberedCredentials() {
        state.value = state.value.copy(username = "alice", password = "correct horse")
        render()

        compose.onNodeWithTag("username").assertTextContains("alice")
        compose.onNodeWithTag("password").assertTextContains("correct horse")
        compose.onNodeWithTag("remember").assertIsOn()

        // A remembered form signs in with one tap and no typing.
        compose.onNodeWithTag("submit").performScrollTo().performClick()
        assertEquals(1, submissions)
    }

    @Test
    fun theEyeIconRevealsAndHidesThePassword() {
        state.value = state.value.copy(password = "correct horse")
        render()

        // Hidden: the field offers to show the value.
        compose.onNodeWithContentDescription("Show password").assertExists()
        compose.onNodeWithTag("passwordToggle").performClick()

        // Revealed: the icon now offers the opposite, and the plain value is on
        // screen for the owner to check.
        compose.onNodeWithContentDescription("Hide password").assertExists()
        compose.onNodeWithTag("password").assertTextContains("correct horse")

        compose.onNodeWithTag("passwordToggle").performClick()
        compose.onNodeWithContentDescription("Show password").assertExists()
    }

    @Test
    fun rememberingCanBeTurnedOff() {
        render()
        compose.onNodeWithTag("remember").assertIsOn()

        compose.onNodeWithTag("remember").performClick()
        assertFalse(state.value.remember)
        compose.onNodeWithTag("remember").assertIsOff()

        compose.onNodeWithTag("remember").performClick()
        assertTrue(state.value.remember)
    }

    @Test
    fun showsServerErrorAndNotice() {
        render(notice = "Your session expired. Sign in again.")

        compose.onNodeWithText("Your session expired. Sign in again.").assertExists()
    }
}
