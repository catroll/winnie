package dev.winnie.app.ui.account

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import dev.winnie.app.data.User
import dev.winnie.app.data.VoiceOutput
import dev.winnie.app.ui.theme.WinnieTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.GraphicsMode

@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class AccountScreenTest {

    @get:Rule
    val compose = createComposeRule()

    private var forgot = 0
    private var signedOut = 0
    private var alerts = 0
    private var previews = 0
    private var voiceOutput = VoiceOutput()

    private fun render(savedPassword: Boolean = true) {
        compose.setContent {
            WinnieTheme {
                AccountScreen(
                    user = User(
                        id = "u1",
                        username = "alice",
                        displayName = null,
                        createdAt = "2026-10-31T00:00:00",
                    ),
                    serverUrl = "http://10.0.2.2:8000",
                    savedPassword = savedPassword,
                    onForgetPassword = { forgot++ },
                    onOpenAlerts = { alerts++ },
                    wakeEnabled = false,
                    onWakeChange = {},
                    voiceOutput = voiceOutput,
                    onVoiceOutputChange = { voiceOutput = it },
                    onPreviewVoice = { previews++ },
                    onSignOut = { signedOut++ },
                    bottomBar = {},
                )
            }
        }
    }

    @Test
    fun showsTheAccountAndTheSavedPasswordState() {
        render()

        compose.onNodeWithText("Account").assertIsDisplayed()
        compose.onNodeWithText("alice").assertIsDisplayed()
        compose.onNodeWithText("http://10.0.2.2:8000").assertIsDisplayed()
        compose.onNodeWithText("Saved password").assertIsDisplayed()
        compose.onNodeWithText(
            "Encrypted with a key in this phone's Keystore",
        ).assertIsDisplayed()
    }

    @Test
    fun anUnsavedPasswordIsExplainedInsteadOfOfferingForget() {
        render(savedPassword = false)

        compose.onNodeWithText(
            "Not saved. Tick Remember on the sign-in screen.",
        ).assertIsDisplayed()
        compose.onNodeWithText("Forget").assertDoesNotExist()
    }

    @Test
    fun forgettingThePasswordAsksFirst() {
        render()

        compose.onNodeWithText("Forget").performScrollTo().performClick()
        compose.onNodeWithText("Forget the saved password?").assertIsDisplayed()
        compose.onNodeWithTag("confirmForget").performClick()

        assertEquals(1, forgot)
    }

    @Test
    fun signingOutAsksFirst() {
        render()

        compose.onNodeWithTag("accountList")
            .performScrollToNode(hasTestTag("signOut"))
        compose.onNodeWithTag("signOut").performClick()
        compose.onNodeWithText("Sign out?").assertIsDisplayed()
        compose.onNodeWithTag("confirmSignOut").performClick()

        assertEquals(1, signedOut)
    }

    @Test
    fun theVoiceSwitchAndRateAreSaved() {
        render()

        compose.onNodeWithTag("accountList")
            .performScrollToNode(hasTestTag("readAloudSwitch"))
        compose.onNodeWithTag("readAloudSwitch").performClick()
        assertEquals(false, voiceOutput.enabled)

        // The rate chips are disabled while reading is off, so turn it back on
        // before choosing one: a rate nobody will hear is not a setting.
        compose.onNodeWithTag("readAloudSwitch").performClick()
        compose.onAllNodesWithTag("voiceRate")[3].performScrollTo().performClick()
        assertEquals(1.5f, voiceOutput.rate)
        assertEquals(true, voiceOutput.enabled)
    }

    @Test
    fun thePreviewButtonAsksForASample() {
        render()

        compose.onNodeWithTag("accountList")
            .performScrollToNode(hasTestTag("voicePreview"))
        compose.onNodeWithTag("voicePreview").performClick()

        assertEquals(1, previews)
    }

    @Test
    fun alertsOpenFromTheAccountTab() {
        render()

        compose.onNodeWithText("Alerts & background").performClick()
        assertEquals(1, alerts)
    }
}
