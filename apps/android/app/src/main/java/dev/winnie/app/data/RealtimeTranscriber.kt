package dev.winnie.app.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString

/** Events produced by the live-caption WebSocket. */
sealed interface RealtimeEvent {
    data object Ready : RealtimeEvent
    data class Text(val text: String, val final: Boolean) : RealtimeEvent
    data class Failed(val message: String) : RealtimeEvent
    data object Closed : RealtimeEvent
}

/**
 * Thin OkHttp WebSocket wrapper for `/meetings/{id}/stream`.
 *
 * Binary frames are raw 16 kHz mono PCM. The server returns cumulative JSON
 * captions; the final transcript still comes from the uploaded WAV, so a
 * failed stream is a degraded caption experience, not a lost recording.
 */
class RealtimeTranscriber(
    api: WinnieApi,
    meetingId: String,
    private val onEvent: (RealtimeEvent) -> Unit,
) {
    private val json = Json { ignoreUnknownKeys = true }

    @Volatile
    private var closing = false

    private val socket: WebSocket = api.openMeetingStream(
        meetingId,
        object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                val message = runCatching {
                    json.decodeFromString<StreamMessage>(text)
                }.getOrNull() ?: return
                when (message.type) {
                    "ready" -> onEvent(RealtimeEvent.Ready)
                    "partial" -> message.text?.let {
                        onEvent(RealtimeEvent.Text(it, final = false))
                    }
                    "final" -> message.text?.let {
                        onEvent(RealtimeEvent.Text(it, final = true))
                    }
                    "error" -> {
                        closing = true
                        onEvent(
                            RealtimeEvent.Failed(
                                message.message ?: "live captions failed",
                            ),
                        )
                    }
                }
            }

            override fun onFailure(
                webSocket: WebSocket,
                t: Throwable,
                response: Response?,
            ) {
                if (closing) return
                closing = true
                onEvent(
                    RealtimeEvent.Failed(
                        t.message ?: "live captions are unavailable",
                    ),
                )
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                onEvent(RealtimeEvent.Closed)
            }
        },
    )

    /** Queues one PCM chunk; OkHttp returns false once the socket is closing. */
    fun send(pcm: ByteArray) {
        if (closing) return
        socket.send(pcm.toByteString())
    }

    /** Asks the server to finalize and closes the socket. */
    fun finish() {
        if (closing) return
        closing = true
        runCatching { socket.send("""{"type":"stop"}""") }
        socket.close(1000, "stop")
    }

    fun cancel() {
        closing = true
        socket.cancel()
    }
}

@Serializable
private data class StreamMessage(
    val type: String,
    val text: String? = null,
    val message: String? = null,
)
