package dev.winnie.app.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.winnie.app.data.Meeting
import dev.winnie.app.data.SessionManager
import dev.winnie.app.data.UnauthorizedException
import dev.winnie.app.data.friendlyMessage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class HomeUiState(
    val loading: Boolean = true,
    val meetings: List<Meeting> = emptyList(),
    val error: String? = null,
)

class HomeViewModel(private val sessions: SessionManager) : ViewModel() {

    private val _state = MutableStateFlow(HomeUiState())
    val state: StateFlow<HomeUiState> = _state.asStateFlow()

    private var refreshJob: Job? = null

    init {
        refresh()
    }

    fun refresh() {
        if (refreshJob?.isActive == true) return
        refreshJob = viewModelScope.launch {
            _state.update { it.copy(loading = true) }
            try {
                val meetings = sessions.requireApi().listMeetings().items
                _state.value = HomeUiState(loading = false, meetings = meetings)
            } catch (e: UnauthorizedException) {
                sessions.onUnauthorized()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(loading = false, error = e.friendlyMessage()) }
            }
        }
    }
}
