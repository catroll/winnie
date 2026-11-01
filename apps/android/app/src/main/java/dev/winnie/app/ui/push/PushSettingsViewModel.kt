package dev.winnie.app.ui.push

import android.content.Context
import android.os.PowerManager
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.winnie.app.AppContainer
import dev.winnie.app.AppStrings
import dev.winnie.app.R
import dev.winnie.app.push.PushController
import dev.winnie.app.push.PushNotifications
import dev.winnie.app.push.PushStatusBus
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

data class PushSettingsUi(
    val loading: Boolean = true,
    val enabled: Boolean = false,
    val connection: String = "disconnected",
    val error: String = "",
    val lastEventLabel: String = "",
    val lastSeq: Long = 0,
    val notificationsAllowed: Boolean = true,
    val batteryUnrestricted: Boolean = false,
    val serverConnected: Int = 0,
    val pending: Int = 0,
    val message: String? = null,
)

/**
 * State for the background-connection screen.
 *
 * It reports three independent things and never conflates them: the user's
 * switch, whether the socket is actually connected, and whether Android lets the
 * app show notifications at all. A green switch with notifications blocked is a
 * common and confusing state, so it is shown explicitly.
 */
class PushSettingsViewModel(
    private val container: AppContainer,
    private val context: Context,
) : ViewModel() {

    private val prefs = container.pushPrefs
    private val _ui = MutableStateFlow(localState())
    val ui: StateFlow<PushSettingsUi> = _ui.asStateFlow()

    init {
        viewModelScope.launch {
            PushStatusBus.state.collect { connection ->
                _ui.update {
                    it.copy(
                        connection = connection.state,
                        error = connection.error.ifBlank { it.error },
                        lastEventLabel = formatEpoch(
                            connection.lastEventAt ?: prefs.lastEventAt
                        ),
                    )
                }
            }
        }
        refresh()
    }

    fun refresh() {
        _ui.update { current ->
            localState().copy(message = current.message)
        }
        viewModelScope.launch {
            val status = withContext(Dispatchers.IO) {
                runCatching { container.phoneApi()?.pushStatus() }.getOrNull()
            }
            _ui.update {
                it.copy(
                    loading = false,
                    serverConnected = status?.connected ?: it.serverConnected,
                    pending = status?.pending ?: it.pending,
                )
            }
        }
    }

    fun setEnabled(enabled: Boolean) {
        if (enabled) {
            PushController.start(context)
        } else {
            PushController.stop(context)
        }
        _ui.update {
            it.copy(
                message = if (enabled) {
                    AppStrings.get(R.string.alerts_message_starting)
                } else {
                    AppStrings.get(R.string.alerts_message_stopped)
                },
            )
        }
        refresh()
    }

    fun sendTest() {
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    container.phoneApi()?.sendTestPush(
                        title = AppStrings.get(R.string.alerts_test_title),
                        body = AppStrings.get(R.string.alerts_test_body),
                    )
                }
            }
            _ui.update {
                it.copy(
                    message = if (result.isSuccess) {
                        AppStrings.get(R.string.alerts_message_test_sent)
                    } else {
                        AppStrings.get(
                            R.string.alerts_message_test_failed,
                            result.exceptionOrNull()?.message
                                ?: AppStrings.get(R.string.common_unknown_error),
                        )
                    },
                )
            }
            refresh()
        }
    }

    fun permissionDenied() {
        _ui.update {
            it.copy(
                message = AppStrings.get(R.string.alerts_message_blocked),
            )
        }
    }

    private fun localState(): PushSettingsUi = PushSettingsUi(
        loading = false,
        enabled = prefs.enabled,
        connection = prefs.status,
        error = prefs.lastError,
        lastEventLabel = formatEpoch(prefs.lastEventAt),
        lastSeq = prefs.lastSeq,
        notificationsAllowed = PushNotifications.notificationsAllowed(context),
        batteryUnrestricted = ignoresBatteryOptimizations(),
    )

    private fun ignoresBatteryOptimizations(): Boolean {
        val manager = context.getSystemService(PowerManager::class.java) ?: return true
        return manager.isIgnoringBatteryOptimizations(context.packageName)
    }

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
