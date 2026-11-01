package dev.winnie.app.ui.chat

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import dev.winnie.app.ui.theme.WinnieTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.GraphicsMode

/**
 * Renders the assistant tab in a Robolectric Android runtime. Markdown is
 * rendered by the real library, so this also proves the renderer works in the
 * app's theme rather than only in a preview.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class ChatScreenTest {

    @get:Rule
    val compose = createComposeRule()

    private var state by mutableStateOf(ChatUiState())
    private var sent = 0
    private var dismissed = 0
    private var openedHistory = 0
    private var newChats = 0
    private var voiceStarts = 0
    private var voiceCancels = 0
    private var voiceDismissals = 0

    private fun render() {
        compose.setContent {
            WinnieTheme {
                ChatScreen(
                    ui = state,
                    onInputChange = { state = state.copy(input = it) },
                    onSend = { sent++ },
                    onNewChat = { newChats++ },
                    onOpenHistory = { openedHistory++ },
                    onDismissError = { dismissed++ },
                    onStartVoice = { voiceStarts++ },
                    onCancelVoice = { voiceCancels++ },
                    onDismissVoiceFailure = { voiceDismissals++ },
                    bottomBar = {},
                )
            }
        }
    }

    @Test
    fun anEmptyChatOffersSuggestions() {
        render()

        compose.onNodeWithText("Winnie").assertIsDisplayed()
        compose.onNodeWithText("Ask Winnie anything").assertIsDisplayed()

        val suggestion = "What is on my schedule today?"
        compose.onNodeWithText(suggestion).performClick()
        assertEquals(suggestion, state.input)
    }

    @Test
    fun rendersAssistantMarkdown() {
        state = ChatUiState(
            conversationId = "c1",
            messages = listOf(
                ChatMessage(0, role = "user", text = "Summarise the meeting"),
                ChatMessage(
                    1,
                    role = "assistant",
                    text = """
                        # Summary

                        - Point one
                        - Point two

                        `inline code`
                    """.trimIndent(),
                ),
            ),
        )
        render()

        compose.onNodeWithText("Summarise the meeting").assertIsDisplayed()
        compose.onNodeWithText("Summary").assertIsDisplayed()
        compose.onNodeWithText("Point one", substring = true).assertIsDisplayed()
        compose.onNodeWithText("Point two", substring = true).assertIsDisplayed()
        compose.onNodeWithText("inline code", substring = true).assertIsDisplayed()
    }

    @Test
    fun aPendingTurnShowsThinkingAndBlocksTheComposer() {
        state = ChatUiState(
            messages = listOf(ChatMessage(0, role = "user", text = "Hello")),
            sending = true,
        )
        render()

        compose.onNodeWithText("Winnie is thinking…").assertIsDisplayed()
        compose.onNodeWithTag("chatSend").assertIsNotEnabled()
    }

    @Test
    fun theSendButtonSendsWhatWasTyped() {
        render()

        compose.onNodeWithTag("chatInput").performTextInput("Hello Winnie")
        assertEquals("Hello Winnie", state.input)

        compose.onNodeWithTag("chatSend").performClick()
        assertEquals(1, sent)
    }

    @Test
    fun toolOutputStaysCollapsedUntilOpened() {
        state = ChatUiState(
            messages = listOf(
                ChatMessage(0, role = "user", text = "Weather?"),
                ChatMessage(1, role = "tool", name = "weather", text = "sunny 21C"),
                ChatMessage(2, role = "assistant", text = "It is **sunny**."),
            ),
        )
        render()

        compose.onNodeWithText("Tool · weather").assertIsDisplayed()
        compose.onNodeWithText("sunny 21C").assertDoesNotExist()

        compose.onNodeWithText("Show").performClick()
        compose.onNodeWithText("sunny 21C").assertIsDisplayed()
    }

    @Test
    fun anErrorCanBeDismissed() {
        state = ChatUiState(error = "Could not connect to the server.")
        render()

        compose.onNodeWithText("Could not connect to the server.").assertIsDisplayed()
        compose.onNodeWithText("Dismiss").performClick()
        assertEquals(1, dismissed)
    }

    @Test
    fun theHistoryActionIsAvailable() {
        render()
        compose.onNodeWithTag("openHistory").performClick()
        assertTrue(openedHistory == 1)
    }

    @Test
    fun aScheduledTaskRunOffersANewChatInsteadOfAComposer() {
        state = ChatUiState(
            messages = listOf(
                ChatMessage(0, role = "assistant", text = "Gold moved **1.4%** in an hour."),
            ),
            readOnly = true,
        )
        render()

        compose.onNodeWithText(
            "This is a scheduled task run. Start a new chat to reply.",
        ).assertIsDisplayed()
        compose.onNodeWithTag("chatSend").assertDoesNotExist()

        compose.onNodeWithText("New chat").performClick()
        assertEquals(1, newChats)
    }

    @Test
    fun aVoiceTurnReplacesTheComposerAndOffersCancel() {
        state = ChatUiState(voice = VoiceState.Listening)
        render()

        compose.onNodeWithTag("voiceState").assertIsDisplayed()
        compose.onNodeWithTag("voiceCancel").performClick()
        assertEquals(1, voiceCancels)
    }

    @Test
    fun theMicrophoneButtonStartsAVoiceTurn() {
        render()

        compose.onNodeWithTag("chatVoice").performClick()
        assertEquals(1, voiceStarts)
    }

    @Test
    fun aFailedVoiceTurnShowsTheReasonAndDismisses() {
        state = ChatUiState(voice = VoiceState.Failed("no speech"))
        render()

        compose.onNodeWithTag("voiceError").assertIsDisplayed()
        compose.onNodeWithTag("voiceDismiss").performClick()
        assertEquals(1, voiceDismissals)
    }
}
