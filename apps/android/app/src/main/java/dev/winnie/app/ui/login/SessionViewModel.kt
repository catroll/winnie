package dev.winnie.app.ui.login

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.winnie.app.data.AppSettings
import dev.winnie.app.data.CredentialStore
import dev.winnie.app.data.DEFAULT_SERVER_URL
import dev.winnie.app.data.SessionManager
import dev.winnie.app.data.SessionState
import dev.winnie.app.data.friendlyMessage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class LoginUiState(
    val submitting: Boolean = false,
    val error: String? = null,
    val serverUrl: String = DEFAULT_SERVER_URL,
    val registerMode: Boolean = false,
    val username: String = "",
    val password: String = "",
    /** Whether a successful sign-in is remembered on this device. */
    val remember: Boolean = true,
)

class SessionViewModel(
    private val sessions: SessionManager,
    settings: AppSettings,
    private val credentials: CredentialStore,
) : ViewModel() {

    val sessionState: StateFlow<SessionState> = sessions.state

    private val _ui = MutableStateFlow(LoginUiState(serverUrl = settings.serverUrl))
    val ui: StateFlow<LoginUiState> = _ui.asStateFlow()

    init {
        viewModelScope.launch { sessions.restore() }
        loadRemembered()
    }

    /**
     * Fills the form from the Keystore-backed credentials store. Runs off the
     * main thread because decrypting may touch the Keystore on first use.
     */
    private fun loadRemembered() {
        viewModelScope.launch {
            val saved = withContext(Dispatchers.IO) {
                runCatching { credentials.saved() }.getOrNull()
            }
            val remember = if (credentials.hasChoice) credentials.remember else true
            _ui.update {
                it.copy(
                    username = saved?.username ?: it.username,
                    password = saved?.password ?: it.password,
                    remember = remember,
                )
            }
        }
    }

    fun setServerUrl(value: String) {
        _ui.update { it.copy(serverUrl = value, error = null) }
    }

    fun setRegisterMode(register: Boolean) {
        _ui.update { it.copy(registerMode = register, error = null) }
    }

    fun setUsername(value: String) {
        _ui.update { it.copy(username = value, error = null) }
    }

    fun setPassword(value: String) {
        _ui.update { it.copy(password = value, error = null) }
    }

    fun setRemember(value: Boolean) {
        _ui.update { it.copy(remember = value) }
    }

    fun applyServerUrl(url: String) {
        _ui.update { if (it.serverUrl == url) it else it.copy(serverUrl = url) }
    }

    fun submit() {
        val current = _ui.value
        if (current.submitting) return

        val name = current.username.trim()
        val validation = when {
            current.serverUrl.isBlank() -> "Enter the Winnie server address."
            name.isBlank() -> "Enter a username."
            current.password.isEmpty() -> "Enter a password."
            current.registerMode && current.password.length < 8 ->
                "Passwords need at least 8 characters."
            else -> null
        }
        if (validation != null) {
            _ui.update { it.copy(error = validation) }
            return
        }

        _ui.update { it.copy(submitting = true, error = null) }
        viewModelScope.launch {
            val result = if (current.registerMode) {
                sessions.register(current.serverUrl, name, current.password, current.remember)
            } else {
                sessions.login(current.serverUrl, name, current.password, current.remember)
            }
            _ui.update {
                it.copy(
                    submitting = false,
                    error = result.exceptionOrNull()?.friendlyMessage(),
                )
            }
            if (result.isSuccess) {
                // The password is only kept in this state while the form is on
                // screen; the persisted copy is the encrypted one.
                _ui.update { it.copy(password = "") }
                loadRemembered()
            }
        }
    }

    fun signOut() {
        viewModelScope.launch {
            sessions.logout()
            // A sign-out is the moment to drop the in-memory password. If the
            // owner asked to be remembered, loadRemembered() puts it back.
            _ui.update { it.copy(password = "") }
            loadRemembered()
        }
    }
}
