package dev.winnie.app.ui.meeting

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.winnie.app.AppStrings
import dev.winnie.app.R
import dev.winnie.app.data.Meeting
import dev.winnie.app.data.Minutes
import dev.winnie.app.data.Segment
import dev.winnie.app.data.SessionManager
import dev.winnie.app.data.UnauthorizedException
import dev.winnie.app.data.friendlyMessage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class MeetingUiState(
    val loading: Boolean = true,
    val meeting: Meeting? = null,
    val segments: List<Segment> = emptyList(),
    val minutes: Minutes? = null,
    val error: String? = null,
)

/**
 * Loads a meeting and its transcript. While the backend is still processing,
 * it polls the status endpoint; the transcript is fetched once the meeting is
 * ready. Polling stops when the screen is cleared.
 */
class MeetingViewModel(
    private val sessions: SessionManager,
    private val meetingId: String,
) : ViewModel() {

    private val _state = MutableStateFlow(MeetingUiState())
    val state: StateFlow<MeetingUiState> = _state.asStateFlow()

    private var loadJob: Job? = null

    init {
        refresh()
    }

    fun refresh() {
        if (loadJob?.isActive == true) return
        loadJob = viewModelScope.launch {
            try {
                load()
            } catch (e: CancellationException) {
                throw e
            } catch (e: UnauthorizedException) {
                sessions.onUnauthorized()
            } catch (e: Exception) {
                _state.update {
                    it.copy(loading = false, error = e.friendlyMessage())
                }
            }
        }
    }

    private suspend fun load() {
        _state.update { it.copy(loading = it.meeting == null, error = null) }
        val api = sessions.requireApi()

        var meeting = api.meeting(meetingId)
        _state.update { it.copy(meeting = meeting, loading = false) }

        var waited = 0L
        while (!meeting.isReady && !meeting.isFailed && waited < MAX_POLL_MILLIS) {
            delay(POLL_INTERVAL_MILLIS)
            waited += POLL_INTERVAL_MILLIS
            meeting = api.meeting(meetingId)
            _state.update { it.copy(meeting = meeting) }
        }

        if (meeting.isFailed) {
            _state.update {
                it.copy(
                    error = meeting.error
                        ?: AppStrings.get(R.string.meeting_processing_failed),
                )
            }
            return
        }
        if (!meeting.isReady) {
            _state.update {
                it.copy(
                    error = AppStrings.get(R.string.meeting_processing_slow),
                )
            }
            return
        }

        val transcript = api.transcript(meetingId)
        val minutes = runCatching { api.minutes(meetingId) }.getOrNull()
        _state.update {
            it.copy(segments = transcript.segments, minutes = minutes, error = null)
        }
    }

    private companion object {
        const val POLL_INTERVAL_MILLIS = 2_000L
        const val MAX_POLL_MILLIS = 10 * 60 * 1_000L
    }
}
