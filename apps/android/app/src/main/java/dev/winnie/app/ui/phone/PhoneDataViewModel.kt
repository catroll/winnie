package dev.winnie.app.ui.phone

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.winnie.app.AppContainer
import dev.winnie.app.phone.PhonePermissions
import dev.winnie.app.phone.PhoneScope
import dev.winnie.app.phone.PhoneSyncScheduler
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class PhoneScopeUi(
    val scope: PhoneScope,
    val enabled: Boolean,
    val ready: Boolean,
    val detail: String,
)

data class PhoneDataUi(
    val loading: Boolean = true,
    val master: Boolean = false,
    val scopes: List<PhoneScopeUi> = emptyList(),
    val running: Boolean = false,
    val lastSyncLabel: String = "never",
    val lastError: String = "",
    val lastResult: String = "",
    val deviceName: String = "",
    val counts: Map<String, Int> = emptyMap(),
    val pendingCommands: Int = 0,
    val message: String? = null,
)

/**
 * Drives the phone-data screen: scope opt-in, permission state, a manual sync
 * and the destructive "delete on server" action.
 *
 * The screen owns no sync logic; the engine does. This class reads preferences
 * for the local view and the server summary for the record counts, and treats a
 * failed summary as "the server side is unknown", not as a sync failure.
 */
class PhoneDataViewModel(
    private val container: AppContainer,
    private val context: Context,
) : ViewModel() {

    private val prefs = container.phonePrefs
    private val _ui = MutableStateFlow(localState())
    val ui: StateFlow<PhoneDataUi> = _ui.asStateFlow()

    init {
        refresh()
    }

    fun refresh() {
        val local = localState()
        _ui.update { current ->
            local.copy(
                running = current.running,
                message = current.message,
                deviceName = current.deviceName,
                counts = current.counts,
                pendingCommands = current.pendingCommands,
            )
        }
        viewModelScope.launch {
            val summary = withContext(Dispatchers.IO) {
                runCatching { container.phoneApi()?.phoneSummary() }.getOrNull()
            }
            _ui.update { state ->
                state.copy(
                    loading = false,
                    deviceName = summary?.devices?.firstOrNull()?.device?.name
                        ?: state.deviceName,
                    counts = summary?.counts?.associate { it.kind to it.count }
                        ?: state.counts,
                    pendingCommands = summary?.pendingCommands ?: state.pendingCommands,
                )
            }
        }
    }

    fun setMaster(enabled: Boolean) {
        if (enabled) {
            if (prefs.enabledScopes().isEmpty()) {
                _ui.update {
                    it.copy(message = "Choose at least one scope below to start syncing.")
                }
                return
            }
            prefs.masterEnabled = true
            PhoneSyncScheduler.configure(context, prefs.intervalMinutes)
            PhoneSyncScheduler.requestSync(context, delaySeconds = 1)
            _ui.update { it.copy(message = "Sync enabled.") }
        } else {
            prefs.masterEnabled = false
            PhoneSyncScheduler.cancel(context)
            _ui.update { it.copy(message = "Sync paused. Nothing is uploaded.") }
        }
        refresh()
    }

    fun enableScope(scope: PhoneScope) {
        if (!PhonePermissions.ready(context, scope)) {
            _ui.update {
                it.copy(message = "${scope.title}: ${PhonePermissions.blocker(context, scope)}")
            }
            refresh()
            return
        }
        prefs.setEnabled(scope, true)
        if (!prefs.masterEnabled) {
            prefs.masterEnabled = true
            PhoneSyncScheduler.configure(context, prefs.intervalMinutes)
        }
        PhoneSyncScheduler.requestSync(context, delaySeconds = 1)
        _ui.update { it.copy(message = "${scope.title} is now shared. First sync queued.") }
        refresh()
    }

    fun disableScope(scope: PhoneScope) {
        prefs.setEnabled(scope, false)
        if (prefs.enabledScopes().isEmpty()) {
            prefs.masterEnabled = false
            PhoneSyncScheduler.cancel(context)
        }
        _ui.update { it.copy(message = "${scope.title} is no longer shared. Server copies stay until deleted.") }
        refresh()
    }

    fun permissionDenied(scope: PhoneScope) {
        _ui.update {
            it.copy(
                message = "${scope.title} needs its permission to sync. " +
                    "Grant it in system settings, then switch it on again.",
            )
        }
        refresh()
    }

    fun syncNow() {
        if (_ui.value.running) return
        _ui.update { it.copy(running = true, message = null) }
        viewModelScope.launch {
            val outcome = withContext(Dispatchers.IO) {
                container.newPhoneSyncEngine().syncNow()
            }
            val message = when {
                outcome.skipped -> "Nothing to sync: enable a scope first."
                outcome.unauthorized -> "The session expired. Sign in again."
                outcome.retry -> "Network unavailable. The background sync will retry."
                outcome.errors.isNotEmpty() -> outcome.errors.joinToString("; ")
                outcome.accepted == 0 && outcome.commands == 0 -> "Nothing new to upload."
                else -> buildString {
                    append("Synced ").append(outcome.accepted).append(" records")
                    if (outcome.commands > 0) {
                        append(", ran ").append(outcome.commands).append(" commands")
                    }
                    append(".")
                }
            }
            _ui.update { it.copy(running = false, message = message) }
            refresh()
        }
    }

    fun wipeServerData() {
        val deviceId = prefs.deviceId
        if (deviceId == null) {
            _ui.update { it.copy(message = "This phone has not synced yet.") }
            return
        }
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching { container.phoneApi()?.wipePhoneDevice(deviceId) }
            }
            prefs.resetCursors()
            _ui.update {
                it.copy(
                    message = if (result.isSuccess) {
                        "Server copies deleted. The next sync uploads the selected scopes again."
                    } else {
                        "Could not delete: ${result.exceptionOrNull()?.message ?: "unknown error"}"
                    },
                )
            }
            refresh()
        }
    }

    private fun localState(): PhoneDataUi = PhoneDataUi(
        loading = false,
        master = prefs.masterEnabled,
        scopes = PhoneScope.entries.map { scope ->
            PhoneScopeUi(
                scope = scope,
                enabled = prefs.isEnabled(scope),
                ready = PhonePermissions.ready(context, scope),
                detail = PhonePermissions.blocker(context, scope),
            )
        },
        lastSyncLabel = formatEpoch(prefs.lastSyncAt),
        lastError = prefs.lastError,
        lastResult = prefs.lastResult,
    )

    private companion object {
        fun formatEpoch(millis: Long?): String {
            if (millis == null) return "never"
            return DateTimeFormatter
                .ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT)
                .withZone(ZoneId.systemDefault())
                .format(Instant.ofEpochMilli(millis))
        }
    }
}
