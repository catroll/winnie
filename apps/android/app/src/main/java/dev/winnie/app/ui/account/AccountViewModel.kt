package dev.winnie.app.ui.account

import androidx.lifecycle.ViewModel
import dev.winnie.app.data.CredentialStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

data class AccountUiState(
    /** Whether a password is currently saved on this device. */
    val savedPassword: Boolean = false,
)

/**
 * Owns the one piece of account state that is local rather than server-side:
 * whether the login password is remembered. The identity and the server address
 * come from the live session.
 */
class AccountViewModel(private val credentials: CredentialStore) : ViewModel() {

    private val _ui = MutableStateFlow(
        AccountUiState(savedPassword = runCatching { credentials.saved() }.getOrNull() != null),
    )
    val ui: StateFlow<AccountUiState> = _ui.asStateFlow()

    fun forgetSavedPassword() {
        credentials.clear()
        _ui.update { it.copy(savedPassword = false) }
    }

    /**
     * Re-reads the store. The view model outlives a sign-out (it is scoped to
     * the activity), and the owner may have changed the remember choice on the
     * login screen since the last visit to this tab.
     */
    fun refresh() {
        val saved = runCatching { credentials.saved() }.getOrNull() != null
        _ui.update { it.copy(savedPassword = saved) }
    }
}
