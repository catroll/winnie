package dev.winnie.app.ui.login

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.winnie.app.data.AppSettings
import dev.winnie.app.data.DEFAULT_SERVER_URL
import dev.winnie.app.data.SessionManager
import dev.winnie.app.data.SessionState
import dev.winnie.app.data.friendlyMessage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class LoginUiState(
    val submitting: Boolean = false,
    val error: String? = null,
    val serverUrl: String = DEFAULT_SERVER_URL,
    val registerMode: Boolean = false,
)

class SessionViewModel(
    private val sessions: SessionManager,
    settings: AppSettings,
) : ViewModel() {

    val sessionState: StateFlow<SessionState> = sessions.state

    private val _ui = MutableStateFlow(LoginUiState(serverUrl = settings.serverUrl))
    val ui: StateFlow<LoginUiState> = _ui.asStateFlow()

    init {
        viewModelScope.launch { sessions.restore() }
    }

    fun setServerUrl(value: String) {
        _ui.update { it.copy(serverUrl = value, error = null) }
    }

    fun setRegisterMode(register: Boolean) {
        _ui.update { it.copy(registerMode = register, error = null) }
    }

    fun applyServerUrl(url: String) {
        _ui.update { if (it.serverUrl == url) it else it.copy(serverUrl = url) }
    }

    fun submit(username: String, password: String) {
        val current = _ui.value
        if (current.submitting) return

        val name = username.trim()
        val validation = when {
            current.serverUrl.isBlank() -> "Enter the Winnie server address."
            name.isBlank() -> "Enter a username."
            password.isEmpty() -> "Enter a password."
            current.registerMode && password.length < 8 ->
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
                sessions.register(current.serverUrl, name, password)
            } else {
                sessions.login(current.serverUrl, name, password)
            }
            _ui.update {
                it.copy(
                    submitting = false,
                    error = result.exceptionOrNull()?.friendlyMessage(),
                )
            }
        }
    }

    fun signOut() {
        viewModelScope.launch { sessions.logout() }
    }
}
