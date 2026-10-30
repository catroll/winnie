package dev.winnie.app.ui.record

import android.os.SystemClock
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.winnie.app.audio.AudioRecorder
import dev.winnie.app.data.SessionManager
import dev.winnie.app.data.UnauthorizedException
import dev.winnie.app.data.friendlyMessage
import dev.winnie.app.ui.defaultMeetingTitle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

sealed interface RecordUiState {
    data class Idle(val error: String? = null) : RecordUiState
    data class Recording(val elapsedMs: Long = 0) : RecordUiState
    data class Working(val message: String) : RecordUiState
    data class Finished(val meetingId: String) : RecordUiState
    data class Failed(val message: String) : RecordUiState
}

/**
 * The whole MVP pipeline: record, create the meeting, upload the WAV, request
 * transcription, then hand the meeting id to the detail screen. Nothing here
 * blocks the main thread.
 */
class RecordViewModel(
    private val sessions: SessionManager,
    private val recorder: AudioRecorder,
) : ViewModel() {

    private val _state = MutableStateFlow<RecordUiState>(RecordUiState.Idle())
    val state: StateFlow<RecordUiState> = _state.asStateFlow()

    private var startedAt = 0L
    private var ticker: Job? = null
    private var work: Job? = null

    fun start() {
        if (_state.value is RecordUiState.Recording) return
        try {
            recorder.start()
        } catch (e: Exception) {
            _state.value = RecordUiState.Idle(e.friendlyMessage())
            return
        }
        startedAt = SystemClock.elapsedRealtime()
        _state.value = RecordUiState.Recording(0)
        ticker = viewModelScope.launch {
            while (isActive) {
                delay(TICK_MILLIS)
                val elapsed = SystemClock.elapsedRealtime() - startedAt
                _state.update { current ->
                    if (current is RecordUiState.Recording) {
                        current.copy(elapsedMs = elapsed)
                    } else {
                        current
                    }
                }
            }
        }
    }

    fun stopAndProcess(title: String) {
        if (_state.value !is RecordUiState.Recording) return
        ticker?.cancel()
        ticker = null

        work = viewModelScope.launch {
            _state.value = RecordUiState.Working("Saving the recording…")
            val file = try {
                recorder.stop()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.value = RecordUiState.Failed(e.friendlyMessage())
                return@launch
            }

            try {
                val api = sessions.requireApi()
                _state.value = RecordUiState.Working("Creating the meeting…")
                val meeting = api.createMeeting(
                    title = title.trim().ifBlank { defaultMeetingTitle() },
                    localLanguage = "zh",
                )
                _state.value = RecordUiState.Working("Uploading audio…")
                api.uploadAudio(meeting.id, file, file.name)
                // With the inline job backend this call returns after the
                // pipeline finishes; with a worker it returns immediately and
                // the detail screen polls.
                _state.value = RecordUiState.Working("Transcribing and writing minutes…")
                api.transcribe(meeting.id)
                _state.value = RecordUiState.Finished(meeting.id)
                // The server now owns the audio; keep the cache from growing.
                file.delete()
            } catch (e: UnauthorizedException) {
                sessions.onUnauthorized()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.value = RecordUiState.Failed(e.friendlyMessage())
            }
        }
    }

    fun cancel() {
        ticker?.cancel()
        ticker = null
        work?.cancel()
        work = null
        if (_state.value is RecordUiState.Recording) {
            recorder.cancel()
        }
        _state.value = RecordUiState.Idle()
    }

    fun clearError() {
        _state.update { current ->
            if (current is RecordUiState.Idle) current.copy(error = null) else current
        }
    }

    override fun onCleared() {
        recorder.cancel()
    }

    private companion object {
        const val TICK_MILLIS = 200L
    }
}
