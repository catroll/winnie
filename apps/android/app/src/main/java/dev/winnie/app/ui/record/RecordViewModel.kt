package dev.winnie.app.ui.record

import android.os.SystemClock
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dev.winnie.app.audio.AudioRecorder
import dev.winnie.app.data.RealtimeEvent
import dev.winnie.app.data.RealtimeTranscriber
import dev.winnie.app.data.SessionManager
import dev.winnie.app.data.UnauthorizedException
import dev.winnie.app.data.WinnieApi
import dev.winnie.app.data.friendlyMessage
import dev.winnie.app.ui.defaultMeetingTitle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class LiveState {
    Connecting,
    Streaming,
    Unavailable,
}

sealed interface RecordUiState {
    data class Idle(val error: String? = null) : RecordUiState
    data class Recording(
        val meetingId: String,
        val elapsedMs: Long = 0,
        val liveText: String = "",
        val liveState: LiveState = LiveState.Connecting,
        val liveError: String? = null,
    ) : RecordUiState

    data class Working(val message: String) : RecordUiState
    data class Finished(val meetingId: String) : RecordUiState
    data class Failed(
        val message: String,
        val meetingId: String? = null,
    ) : RecordUiState
}

/**
 * Records a meeting and streams PCM to the backend for live captions. The
 * meeting row is created up front so the stream has an owner; stopping still
 * uploads the WAV and runs the file-first pipeline, which remains the
 * canonical transcript. A failed stream therefore costs captions, not the
 * recording.
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
    private var meetingId: String? = null
    private var realtime: RealtimeTranscriber? = null
    private var chunks: Channel<ByteArray>? = null
    private var sender: Job? = null

    fun start(title: String) {
        if (_state.value !is RecordUiState.Idle) return
        _state.value = RecordUiState.Working("Creating the meeting…")
        work = viewModelScope.launch {
            val api = try {
                sessions.requireApi()
            } catch (e: Exception) {
                _state.value = RecordUiState.Idle(e.friendlyMessage())
                return@launch
            }
            val meeting = try {
                api.createMeeting(
                    title = title.trim().ifBlank { defaultMeetingTitle() },
                    localLanguage = "zh",
                    source = "realtime",
                )
            } catch (e: UnauthorizedException) {
                sessions.onUnauthorized()
                return@launch
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.value = RecordUiState.Idle(e.friendlyMessage())
                return@launch
            }

            meetingId = meeting.id
            _state.value = RecordUiState.Recording(meeting.id)

            val channel = Channel<ByteArray>(
                capacity = 64,
                onBufferOverflow = BufferOverflow.DROP_OLDEST,
            )
            chunks = channel
            val transcriber = RealtimeTranscriber(api, meeting.id) { event ->
                onRealtimeEvent(event)
            }
            realtime = transcriber
            sender = viewModelScope.launch(Dispatchers.IO) {
                for (chunk in channel) transcriber.send(chunk)
            }

            try {
                recorder.start { buffer, read ->
                    var offset = 0
                    while (offset < read) {
                        val end = minOf(offset + STREAM_CHUNK_BYTES, read)
                        channel.trySend(buffer.copyOfRange(offset, end))
                        offset = end
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                finishRealtime(cancel = true)
                deleteDraft(api, meeting.id)
                meetingId = null
                _state.value = RecordUiState.Idle(e.friendlyMessage())
                return@launch
            }

            startedAt = SystemClock.elapsedRealtime()
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
    }

    fun stopAndProcess() {
        val current = _state.value as? RecordUiState.Recording ?: return
        ticker?.cancel()
        ticker = null

        work = viewModelScope.launch {
            _state.value = RecordUiState.Working("Saving the recording…")
            val file = try {
                recorder.stop()
            } catch (e: CancellationException) {
                finishRealtime(cancel = true)
                throw e
            } catch (e: Exception) {
                finishRealtime(cancel = true)
                _state.value = RecordUiState.Failed(
                    e.friendlyMessage(),
                    current.meetingId,
                )
                return@launch
            }
            // Tell the stream to stop, then upload the same audio for the
            // accurate file pass. The live captions are never persisted.
            finishRealtime(cancel = false)

            try {
                val api = sessions.requireApi()
                _state.value = RecordUiState.Working("Uploading audio…")
                api.uploadAudio(current.meetingId, file, file.name)
                _state.value = RecordUiState.Working(
                    "Transcribing and writing minutes…",
                )
                api.transcribe(current.meetingId)
                _state.value = RecordUiState.Finished(current.meetingId)
                file.delete()
            } catch (e: UnauthorizedException) {
                sessions.onUnauthorized()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.value = RecordUiState.Failed(
                    e.friendlyMessage(),
                    current.meetingId,
                )
            }
        }
    }

    /** User-initiated abandon: stop everything and delete the draft meeting. */
    fun discard() {
        ticker?.cancel()
        ticker = null
        work?.cancel()
        work = null
        if (_state.value is RecordUiState.Recording) {
            recorder.cancel()
        }
        finishRealtime(cancel = true)
        val draft = meetingId
        meetingId = null
        _state.value = RecordUiState.Idle()
        if (draft != null) {
            val api = runCatching { sessions.requireApi() }.getOrNull()
            if (api != null) {
                viewModelScope.launch {
                    withContext(NonCancellable) {
                        runCatching { api.deleteMeeting(draft) }
                    }
                }
            }
        }
    }

    /** Cleanup after a successful stop; the meeting must survive navigation. */
    fun reset() {
        ticker?.cancel()
        ticker = null
        work = null
        finishRealtime(cancel = true)
        meetingId = null
        _state.value = RecordUiState.Idle()
    }

    fun clearError() {
        _state.update { current ->
            if (current is RecordUiState.Idle) current.copy(error = null) else current
        }
    }

    override fun onCleared() {
        ticker?.cancel()
        finishRealtime(cancel = true)
        recorder.cancel()
    }

    private fun onRealtimeEvent(event: RealtimeEvent) {
        _state.update { current ->
            if (current !is RecordUiState.Recording) return@update current
            when (event) {
                RealtimeEvent.Ready -> current.copy(
                    liveState = LiveState.Streaming,
                    liveError = null,
                )
                is RealtimeEvent.Text -> current.copy(
                    liveText = event.text,
                    liveState = LiveState.Streaming,
                    liveError = null,
                )
                is RealtimeEvent.Failed -> current.copy(
                    liveState = LiveState.Unavailable,
                    liveError = event.message,
                )
                RealtimeEvent.Closed -> if (current.liveState == LiveState.Connecting) {
                    current.copy(
                        liveState = LiveState.Unavailable,
                        liveError = "live captions disconnected",
                    )
                } else {
                    current
                }
            }
        }
    }

    private fun finishRealtime(cancel: Boolean) {
        val transcriber = realtime
        realtime = null
        sender?.cancel()
        sender = null
        chunks?.close()
        chunks = null
        if (transcriber != null) {
            if (cancel) {
                transcriber.cancel()
            } else {
                transcriber.finish()
            }
        }
    }

    private suspend fun deleteDraft(api: WinnieApi, id: String) {
        runCatching { api.deleteMeeting(id) }
    }

    private companion object {
        const val TICK_MILLIS = 200L
        // 100 ms of 16 kHz mono signed 16-bit PCM.
        const val STREAM_CHUNK_BYTES = 3200
    }
}
