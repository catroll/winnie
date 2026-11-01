package dev.winnie.app.ui.phone

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.winnie.app.AppContainer
import dev.winnie.app.AppStrings
import dev.winnie.app.R
import dev.winnie.app.phone.PhonePermissions
import dev.winnie.app.phone.PhoneScope
import dev.winnie.app.phone.PhoneSyncLoop
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
    val lastSyncLabel: String = "",
    val lastError: String = "",
    val lastResult: String = "",
    val deviceName: String = "",
    val counts: Map<String, Int> = emptyMap(),
    val pendingCommands: Int = 0,
    /** The server-suggested interval for the background worker, in minutes. */
    val intervalMinutes: Int = 30,
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
                    it.copy(message = AppStrings.get(R.string.phone_message_choose_scope))
                }
                return
            }
            prefs.masterEnabled = true
            PhoneSyncScheduler.configure(context, prefs.intervalMinutes)
            PhoneSyncScheduler.requestSync(context, delaySeconds = 1)
            PhoneSyncLoop.wake()
            _ui.update { it.copy(message = AppStrings.get(R.string.phone_message_enabled)) }
        } else {
            prefs.masterEnabled = false
            PhoneSyncScheduler.cancel(context)
            _ui.update { it.copy(message = AppStrings.get(R.string.phone_message_paused)) }
        }
        refresh()
    }

    fun enableScope(scope: PhoneScope) {
        if (!PhonePermissions.ready(context, scope)) {
            _ui.update {
                it.copy(message = AppStrings.get(
                    R.string.phone_message_scope_blocked,
                    AppStrings.get(scope.titleRes),
                    PhonePermissions.blocker(context, scope),
                ))
            }
            refresh()
            return
        }
        prefs.setEnabled(scope, true)
        // A scope that was synced before keeps its fingerprint; clearing
        // it makes the first tick after re-enabling upload the snapshot.
        container.phoneChangeDetector.forget(scope)
        if (!prefs.masterEnabled) {
            prefs.masterEnabled = true
            PhoneSyncScheduler.configure(context, prefs.intervalMinutes)
        }
        PhoneSyncScheduler.requestSync(context, delaySeconds = 1)
        PhoneSyncLoop.wake()
        _ui.update {
            it.copy(
                message = AppStrings.get(
                    R.string.phone_message_scope_enabled,
                    AppStrings.get(scope.titleRes),
                ),
            )
        }
        refresh()
    }

    fun disableScope(scope: PhoneScope) {
        prefs.setEnabled(scope, false)
        container.phoneChangeDetector.forget(scope)
        if (prefs.enabledScopes().isEmpty()) {
            prefs.masterEnabled = false
            PhoneSyncScheduler.cancel(context)
        } else {
            // The device row advertises the enabled scopes; refresh it now
            // rather than at the next periodic run.
            PhoneSyncScheduler.requestSync(context, delaySeconds = 1)
        }
        _ui.update {
            it.copy(
                message = AppStrings.get(
                    R.string.phone_message_scope_disabled,
                    AppStrings.get(scope.titleRes),
                ),
            )
        }
        refresh()
    }

    fun permissionDenied(scope: PhoneScope) {
        _ui.update {
            it.copy(
                message = AppStrings.get(
                    R.string.phone_message_needs_permission,
                    AppStrings.get(scope.titleRes),
                ),
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
                outcome.skipped ->
                    AppStrings.get(R.string.phone_message_nothing_to_sync)
                outcome.unauthorized ->
                    AppStrings.get(R.string.phone_message_session_expired)
                outcome.retry ->
                    AppStrings.get(R.string.phone_message_network_retry)
                outcome.errors.isNotEmpty() -> outcome.errors.joinToString("; ")
                outcome.accepted == 0 && outcome.commands == 0 ->
                    AppStrings.get(R.string.phone_message_nothing_new)
                else -> buildString {
                    append(
                        AppStrings.quantity(
                            R.plurals.phone_synced_records,
                            outcome.accepted,
                            outcome.accepted,
                        ),
                    )
                    if (outcome.commands > 0) {
                        append(' ')
                        append(
                            AppStrings.quantity(
                                R.plurals.phone_synced_commands,
                                outcome.commands,
                                outcome.commands,
                            ),
                        )
                    }
                }
            }
            _ui.update { it.copy(running = false, message = message) }
            refresh()
        }
    }

    fun wipeServerData() {
        val deviceId = prefs.deviceId
        if (deviceId == null) {
            _ui.update { it.copy(message = AppStrings.get(R.string.phone_message_not_synced)) }
            return
        }
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching { container.phoneApi()?.wipePhoneDevice(deviceId) }
            }
            prefs.resetCursors()
            // The server holds nothing now, so every scope must upload
            // from scratch; the old fingerprints describe deleted rows.
            prefs.resetSyncState()
            PhoneSyncLoop.wake()
            _ui.update {
                it.copy(
                    message = if (result.isSuccess) {
                        AppStrings.get(R.string.phone_message_deleted)
                    } else {
                        AppStrings.get(
                            R.string.phone_message_delete_failed,
                            result.exceptionOrNull()?.message
                                ?: AppStrings.get(R.string.common_unknown_error),
                        )
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
        intervalMinutes = prefs.intervalMinutes,
    )

    private companion object {
        fun formatEpoch(millis: Long?): String {
            if (millis == null) return AppStrings.get(R.string.common_never)
            return DateTimeFormatter
                .ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT)
                .withZone(ZoneId.systemDefault())
                .format(Instant.ofEpochMilli(millis))
        }
    }
}
