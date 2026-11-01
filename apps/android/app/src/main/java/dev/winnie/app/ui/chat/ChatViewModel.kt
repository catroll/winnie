package dev.winnie.app.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.winnie.app.data.AppSettings
import dev.winnie.app.data.AssistantConversationSummary
import dev.winnie.app.data.SessionManager
import dev.winnie.app.data.UnauthorizedException
import dev.winnie.app.data.friendlyMessage
import kotlinx.coroutines.CancellationException
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
) : ViewModel() {

    private val _ui = MutableStateFlow(ChatUiState())
    val ui: StateFlow<ChatUiState> = _ui.asStateFlow()

    private var nextKey = 0L

    init {
        // Resume the conversation that was open when the app was last used.
        settings.lastConversationId?.let { openConversation(it) }
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
        val current = _ui.value
        val message = current.input.trim()
        if (message.isEmpty() || current.sending || current.loading || current.readOnly) return

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
                    .assistantChat(message, current.conversationId)
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

    /** Loads a recorded conversation, replacing whatever is on screen. */
    fun openConversation(conversationId: String) {
        val current = _ui.value
        if (current.sending || (current.loading && current.conversationId == conversationId)) return
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
