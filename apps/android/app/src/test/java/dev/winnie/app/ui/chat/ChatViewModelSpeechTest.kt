package dev.winnie.app.ui.chat

import dev.winnie.app.data.VoiceOutput
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The rule that decides whether the phone talks without being asked. It is one
 * function so the four cases are pinned: a dictated turn is spoken, a typed one
 * is not, and the switch wins over both.
 */
class ChatViewModelSpeechTest {

    @Test
    fun aDictatedTurnIsAnsweredOutLoud() {
        assertTrue(ChatViewModel.speaksUnasked("voice", VoiceOutput()))
    }

    @Test
    fun aTypedTurnStaysSilent() {
        assertFalse(ChatViewModel.speaksUnasked("text", VoiceOutput()))
    }

    @Test
    fun theSwitchSilencesADictatedTurn() {
        assertFalse(ChatViewModel.speaksUnasked("voice", VoiceOutput(enabled = false)))
    }
}
