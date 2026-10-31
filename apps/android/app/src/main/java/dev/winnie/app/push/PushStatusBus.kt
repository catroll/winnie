package dev.winnie.app.push

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/** What the settings screen shows about the live connection. */
data class PushConnection(
    val state: String = "disconnected",
    val error: String = "",
    val lastEventAt: Long? = null,
)

/**
 * The foreground service publishes its state here; the screen collects it.
 *
 * The process can die while the service reconnects, so the persisted values in
 * [PushPrefs] remain the fallback and this bus only adds live updates.
 */
object PushStatusBus {

    private val _state = MutableStateFlow(PushConnection())
    val state: StateFlow<PushConnection> = _state.asStateFlow()

    fun update(transform: (PushConnection) -> PushConnection) {
        _state.update(transform)
    }
}
