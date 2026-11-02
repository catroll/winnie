package dev.winnie.app.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.winnie.app.AppStrings
import dev.winnie.app.R
import dev.winnie.app.audio.AudioRecorder
import dev.winnie.app.audio.EndpointDetector
import dev.winnie.app.audio.Speaker
import dev.winnie.app.data.AppSettings
import dev.winnie.app.data.AssistantConversationSummary
import dev.winnie.app.data.SessionManager
import dev.winnie.app.data.VoiceOutput
import dev.winnie.app.data.UnauthorizedException
import dev.winnie.app.data.friendlyMessage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * One rendered line of a conversation.
 *
 * [key] exists because the server does not give messages an id, and a
 * LazyColumn needs stable keys; [role] is the server's own vocabulary.
 */
data class ChatMessage(
    val key: Long,
    val role: String,
    val name: String = "",
    val text: String,
    val tools: List<String> = emptyList(),
) {
    val fromUser: Boolean get() = role == "user"
    val isTool: Boolean get() = role == "tool"
}

/**
 * The voice turn that follows a wake word.
 *
 * [Listening] and [Transcribing] are shown as a sheet over the conversation;
 * [Failed] keeps the sheet open with the reason, because a wake that produced
 * nothing must not look like a wake that was never heard.
 */
sealed interface VoiceState {
    data object Idle : VoiceState
    data object Listening : VoiceState
    data object Transcribing : VoiceState
    data class Failed(val message: String) : VoiceState
}

data class ChatUiState(
    val messages: List<ChatMessage> = emptyList(),
    val conversationId: String? = null,
    val input: String = "",
    /** Fetching an existing conversation. */
    val loading: Boolean = false,
    /** A turn is in flight. */
    val sending: Boolean = false,
    /**
     * The conversation is a scheduled task run. The server rejects a message in
     * one (409), so the composer is replaced by a "start a new chat" notice.
     */
    val readOnly: Boolean = false,
    val error: String? = null,
    val history: List<AssistantConversationSummary> = emptyList(),
    val historyLoading: Boolean = false,
    val historyError: String? = null,
    val voice: VoiceState = VoiceState.Idle,
    /** The reply currently being read aloud, if any. */
    val speakingKey: Long? = null,
    /** True while the engine is speaking, including a preview from the settings. */
    val speaking: Boolean = false,
)

/**
 * Drives the assistant tab.
 *
 * A reply arrives as one response, not a stream, so the UI shows the message the
 * owner sent and a thinking row until the server answers. A failed turn is
 * removed again and its text returns to the composer: the server never stored
 * it, and leaving a bubble that disappears on reload would lie about the
 * transcript.
 */
class ChatViewModel(
    private val sessions: SessionManager,
    private val settings: AppSettings,
    private val newRecorder: () -> AudioRecorder,
    private val speaker: Speaker,
) : ViewModel() {

    private val _ui = MutableStateFlow(ChatUiState())
    val ui: StateFlow<ChatUiState> = _ui.asStateFlow()

    private var nextKey = 0L
    private var recorder: AudioRecorder? = null
    private var voiceGuard: Job? = null
    private var voiceUpload: Job? = null

    internal companion object {
        /** One hands-free turn never lasts longer than this. */
        const val VOICE_TURN_LIMIT_MS = 30_000L

        /** The wire value that marks a dictated turn; see `AssistantChatRequest`. */
        const val VOICE_INPUT = "voice"

        /**
         * Whether a reply is spoken without being asked.
         *
         * A dictated turn was asked out loud, so its answer is spoken as soon as
         * it arrives — unless reading is switched off. A typed turn stays silent:
         * the owner is looking at the screen, and a phone that starts talking in
         * a meeting for a message that was deliberately typed is worse than one
         * that waits to be asked. Every reply can still be read on demand.
         */
        fun speaksUnasked(input: String, output: VoiceOutput): Boolean =
            input == VOICE_INPUT && output.enabled
    }

    init {
        // Resume the conversation that was open when the app was last used.
        settings.lastConversationId?.let { openConversation(it) }
        viewModelScope.launch {
            speaker.speaking.collect { speaking ->
                _ui.update {
                    it.copy(
                        speaking = speaking,
                        speakingKey = if (speaking) it.speakingKey else null,
                    )
                }
            }
        }
        viewModelScope.launch {
            speaker.failures.collect { message -> _ui.update { it.copy(error = message) } }
        }
    }

    /**
     * Reads one reply aloud, or stops it when it is the one being read.
     *
     * The button is a toggle because a long answer is the case where the owner
     * most needs silence, and that is the same tap.
     */
    fun speak(message: ChatMessage) {
        if (_ui.value.speakingKey == message.key) {
            stopSpeaking()
            return
        }
        if (message.text.isBlank()) return
        _ui.update { it.copy(speakingKey = message.key) }
        speaker.speak(message.text)
    }

    fun stopSpeaking() {
        speaker.stop()
        _ui.update { it.copy(speakingKey = null, speaking = false) }
    }

    /**
     * Says one sentence in the configured voice so the rate can be judged
     * without sending a turn. It belongs to the chat because the engine does.
     */
    fun previewVoice() {
        _ui.update { it.copy(speakingKey = null) }
        speaker.speak(AppStrings.get(R.string.voice_preview_sample))
    }

    fun onInputChange(value: String) {
        _ui.update { it.copy(input = value) }
    }

    fun dismissError() {
        _ui.update { it.copy(error = null) }
    }

    fun newChat() {
        val current = _ui.value
        if (current.sending) return
        stopSpeaking()
        settings.lastConversationId = null
        _ui.update {
            it.copy(
                messages = emptyList(),
                conversationId = null,
                input = "",
                loading = false,
                readOnly = false,
                error = null,
            )
        }
    }

    fun send() {
        val message = _ui.value.input.trim()
        if (message.isEmpty()) return
        sendText(message)
    }

    /**
     * Sends one completed turn. The composer's text is the source for a typed
     * message; a voice turn arrives here as text after the server transcribed
     * the recording, so both paths share the transcript bookkeeping. [input]
     * tells the server which it was: a dictated turn is answered for the ear.
     */
    fun sendText(message: String, input: String = "text") {
        val current = _ui.value
        if (message.isEmpty() || current.sending || current.loading || current.readOnly) return

        // A new turn interrupts the previous answer: leaving it reading means
        // the owner cannot hear the reply they just asked for.
        stopSpeaking()

        val userKey = nextKey++
        _ui.update {
            it.copy(
                messages = it.messages + ChatMessage(userKey, role = "user", text = message),
                input = "",
                sending = true,
                error = null,
            )
        }
        viewModelScope.launch {
            try {
                val reply = sessions.requireApi()
                    .assistantChat(message, current.conversationId, input = input)
                val replyKey = nextKey++
                _ui.update {
                    it.copy(
                        messages = it.messages + ChatMessage(
                            key = replyKey,
                            role = "assistant",
                            text = reply.reply,
                            tools = reply.toolsUsed,
                        ),
                        conversationId = reply.conversationId,
                        sending = false,
                    )
                }
                settings.lastConversationId = reply.conversationId
                // A dictated turn is answered for the ear: the owner asked out
                // loud, so the answer is spoken as soon as it arrives. A typed
                // turn stays silent — the screen is already in front of them.
                if (speaksUnasked(input, settings.voiceOutput)) {
                    speak(
                        ChatMessage(
                            key = replyKey,
                            role = "assistant",
                            text = reply.reply,
                            tools = reply.toolsUsed,
                        ),
                    )
                }
            } catch (e: UnauthorizedException) {
                // The session is gone, but this view model outlives the login
                // screen: leaving `sending` set would disable the composer after
                // the owner signs back in.
                _ui.update {
                    it.copy(
                        messages = it.messages.filterNot { item -> item.key == userKey },
                        input = message,
                        sending = false,
                    )
                }
                sessions.onUnauthorized()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _ui.update {
                    it.copy(
                        messages = it.messages.filterNot { item -> item.key == userKey },
                        input = message,
                        sending = false,
                        error = e.friendlyMessage(),
                    )
                }
            }
        }
    }

    /**
     * Starts one hands-free turn. [preRoll] is the audio the wake detector held
     * before the detection, so the first words after "winnie" are not lost to
     * the detection latency.
     */
    fun startVoiceTurn(preRoll: ByteArray = ByteArray(0)) {
        val current = _ui.value
        if (current.voice != VoiceState.Idle) return
        if (current.sending || current.loading || current.readOnly) return

        // The owner is talking, so the previous answer must stop.
        stopSpeaking()

        val recorder = newRecorder()
        val endpoint = EndpointDetector()
        this.recorder = recorder
        _ui.update { it.copy(voice = VoiceState.Listening, error = null) }
        try {
            recorder.start(preRoll) { bytes, length ->
                if (endpoint.accept(bytes, length)) finishVoiceTurn()
            }
        } catch (e: Exception) {
            this.recorder = null
            _ui.update { it.copy(voice = VoiceState.Failed(e.friendlyMessage())) }
            return
        }
        // The endpointer ends a normal turn, this guard ends a broken one: a
        // microphone that returns silence forever must not pin the sheet.
        voiceGuard = viewModelScope.launch {
            delay(VOICE_TURN_LIMIT_MS)
            finishVoiceTurn()
        }
    }

    fun cancelVoiceTurn() {
        recorder?.cancel()
        recorder = null
        voiceGuard?.cancel()
        voiceGuard = null
        voiceUpload?.cancel()
        voiceUpload = null
        _ui.update { it.copy(voice = VoiceState.Idle) }
    }

    fun dismissVoiceFailure() {
        if (_ui.value.voice is VoiceState.Failed) {
            _ui.update { it.copy(voice = VoiceState.Idle) }
        }
    }

    private fun finishVoiceTurn() {
        val recorder = recorder ?: return
        this.recorder = null
        voiceGuard?.cancel()
        voiceGuard = null
        _ui.update { it.copy(voice = VoiceState.Transcribing) }
        voiceUpload = viewModelScope.launch {
            val file = try {
                recorder.stop()
            } catch (e: Exception) {
                _ui.update { it.copy(voice = VoiceState.Failed(e.friendlyMessage())) }
                return@launch
            }
            try {
                val transcript = sessions.requireApi().transcribeVoice(file)
                if (transcript.text.isBlank()) {
                    _ui.update {
                        it.copy(
                            voice = VoiceState.Failed(
                                AppStrings.get(R.string.voice_no_speech),
                            ),
                        )
                    }
                } else {
                    _ui.update { it.copy(voice = VoiceState.Idle) }
                    sendText(transcript.text, input = VOICE_INPUT)
                }
            } catch (e: UnauthorizedException) {
                _ui.update { it.copy(voice = VoiceState.Idle) }
                sessions.onUnauthorized()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _ui.update { it.copy(voice = VoiceState.Failed(e.friendlyMessage())) }
            } finally {
                file.delete()
            }
        }
    }

    override fun onCleared() {
        recorder?.cancel()
        recorder = null
        speaker.release()
        super.onCleared()
    }

    /** Loads a recorded conversation, replacing whatever is on screen. */
    fun openConversation(conversationId: String) {
        val current = _ui.value
        if (current.sending || (current.loading && current.conversationId == conversationId)) return
        // The transcript is about to be replaced, so a reply being read from it
        // must not keep talking over the one that is about to appear.
        stopSpeaking()
        _ui.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            try {
                val conversation = sessions.requireApi().assistantConversation(conversationId)
                val messages = conversation.messages.map { message ->
                    ChatMessage(
                        key = nextKey++,
                        role = message.role,
                        name = message.name,
                        text = message.text,
                    )
                }
                _ui.update {
                    it.copy(
                        messages = messages,
                        conversationId = conversation.id,
                        loading = false,
                        sending = false,
                        readOnly = conversation.source == "task",
                    )
                }
                settings.lastConversationId = conversation.id
            } catch (e: UnauthorizedException) {
                _ui.update { it.copy(loading = false) }
                sessions.onUnauthorized()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // A conversation that no longer exists must not trap the tab on
                // an empty list: become a new chat and say why.
                settings.lastConversationId = null
                _ui.update {
                    it.copy(
                        messages = emptyList(),
                        conversationId = null,
                        loading = false,
                        readOnly = false,
                        error = e.friendlyMessage(),
                    )
                }
            }
        }
    }

    fun loadHistory() {
        if (_ui.value.historyLoading) return
        _ui.update { it.copy(historyLoading = true, historyError = null) }
        viewModelScope.launch {
            try {
                val listing = sessions.requireApi().assistantConversations()
                _ui.update { it.copy(history = listing.items, historyLoading = false) }
            } catch (e: UnauthorizedException) {
                _ui.update { it.copy(historyLoading = false) }
                sessions.onUnauthorized()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _ui.update {
                    it.copy(historyLoading = false, historyError = e.friendlyMessage())
                }
            }
        }
    }
}
