package dev.winnie.app.data

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

data class Session(
    val serverUrl: String,
    val user: User,
)

sealed interface SessionState {
    data object Restoring : SessionState
    data class LoggedOut(
        val serverUrl: String,
        val message: String? = null,
    ) : SessionState

    data class LoggedIn(val session: Session) : SessionState
}

/**
 * Owns the bearer token and the one [WinnieApi] instance used by the signed-in
 * screens. Screens never build URLs or headers themselves.
 */
class SessionManager(
    private val tokenStore: TokenStore,
    private val settings: AppSettings,
    private val credentials: CredentialStore,
) {
    private val _state = MutableStateFlow<SessionState>(SessionState.Restoring)
    val state: StateFlow<SessionState> = _state.asStateFlow()

    private val restoreLock = Mutex()
    private var api: WinnieApi? = null

    suspend fun restore() {
        restoreLock.withLock {
            if (_state.value !is SessionState.Restoring) return
            // Reading the token decrypts it with the Keystore key.
            val token = withContext(Dispatchers.IO) { tokenStore.read() }
            val serverUrl = settings.serverUrl
            if (token == null) {
                _state.value = SessionState.LoggedOut(serverUrl)
                return
            }
            try {
                val candidate = WinnieApi(serverUrl, token)
                val user = candidate.me()
                api = candidate
                _state.value = SessionState.LoggedIn(Session(serverUrl, user))
            } catch (e: UnauthorizedException) {
                tokenStore.clear()
                _state.value = SessionState.LoggedOut(
                    serverUrl,
                    "Your session expired. Sign in again.",
                )
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Keep the token: an unreachable server is not an invalid one.
                _state.value = SessionState.LoggedOut(
                    serverUrl,
                    "Could not restore the session: ${e.friendlyMessage()}",
                )
            }
        }
    }

    suspend fun login(
        serverUrl: String,
        username: String,
        password: String,
        remember: Boolean = true,
    ): Result<Unit> = authenticate(serverUrl, username, password, register = false, remember = remember)

    suspend fun register(
        serverUrl: String,
        username: String,
        password: String,
        remember: Boolean = true,
    ): Result<Unit> = authenticate(serverUrl, username, password, register = true, remember = remember)

    private suspend fun authenticate(
        serverUrl: String,
        username: String,
        password: String,
        register: Boolean,
        remember: Boolean,
    ): Result<Unit> {
        val normalized = normalizeServerUrl(serverUrl)
        val client = WinnieApi(normalized)
        return try {
            val token = if (register) {
                client.register(username, password)
            } else {
                client.login(username, password)
            }
            // Saving and remembering both seal with the Keystore key.
            withContext(Dispatchers.IO) {
                tokenStore.save(token.accessToken)
                // Only a successful sign-in is remembered: a typo must not
                // replace the credentials that used to work.
                if (remember) {
                    credentials.save(username, password)
                } else {
                    credentials.clear()
                }
            }
            settings.serverUrl = normalized
            api = WinnieApi(normalized, token.accessToken)
            _state.value = SessionState.LoggedIn(Session(normalized, token.user))
            Result.success(Unit)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    fun requireApi(): WinnieApi =
        api ?: throw IllegalStateException("No signed-in session")

    suspend fun logout() {
        val client = api
        // Clear local state first: revoking the token must never make the user
        // wait on an unreachable server.
        tokenStore.clear()
        api = null
        // The open conversation belongs to this session. Another account can
        // sign in on the same phone, and the server keeps one history, so a
        // stale id would show that account the previous owner's transcript.
        settings.lastConversationId = null
        _state.value = SessionState.LoggedOut(settings.serverUrl)
        runCatching { client?.logout() }
    }

    /** Called when a request returns 401: the token is no longer valid. */
    fun onUnauthorized() {
        tokenStore.clear()
        api = null
        settings.lastConversationId = null
        _state.value = SessionState.LoggedOut(
            settings.serverUrl,
            "Your session expired. Sign in again.",
        )
    }
}
