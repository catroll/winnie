package dev.winnie.app.data

import java.io.IOException
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.io.File
import java.net.URLEncoder
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Error returned by the Winnie API. */
open class ApiException(
    val statusCode: Int,
    message: String,
) : IOException(message)

class UnauthorizedException(message: String) : ApiException(401, message)

/**
 * Thin HTTP client for the Winnie meeting API.
 *
 * It deliberately contains no Android types so the whole flow (login, upload,
 * transcription, polling) can be exercised from a JVM test against a real
 * running backend.
 */
class WinnieApi(
    baseUrl: String,
    private val token: String? = null,
    private val client: OkHttpClient = defaultHttpClient,
) {
    private val root = baseUrl.trim().trimEnd('/')
    private val longClient: OkHttpClient by lazy {
        client.newBuilder()
            .readTimeout(10, TimeUnit.MINUTES)
            .callTimeout(12, TimeUnit.MINUTES)
            .build()
    }
    private val streamingClient: OkHttpClient by lazy {
        client.newBuilder()
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .pingInterval(20, TimeUnit.SECONDS)
            .build()
    }

    /**
     * A model turn runs tools before it answers, so the default 60 s read
     * timeout is too short; the server itself bounds the loop.
     */
    private val chatClient: OkHttpClient by lazy {
        client.newBuilder()
            .readTimeout(5, TimeUnit.MINUTES)
            .callTimeout(6, TimeUnit.MINUTES)
            .build()
    }

    suspend fun login(username: String, password: String): TokenResponse =
        postJson(
            path = "/api/v1/auth/login",
            body = json.encodeToString(LoginRequest(username, password)),
        )

    suspend fun register(username: String, password: String): TokenResponse =
        postJson(
            path = "/api/v1/auth/register",
            body = json.encodeToString(RegisterRequest(username, password)),
        )

    suspend fun me(): User = getJson("/api/v1/auth/me")

    suspend fun listMeetings(limit: Int = 50): MeetingList =
        getJson("/api/v1/meetings?limit=$limit")

    suspend fun createMeeting(
        title: String,
        localLanguage: String,
        source: String = "file",
    ): Meeting =
        postJson(
            path = "/api/v1/meetings",
            body = json.encodeToString(MeetingCreate(title, localLanguage, source)),
        )

    /** Opens the live-caption stream for a meeting created with source=realtime. */
    fun openMeetingStream(
        meetingId: String,
        listener: WebSocketListener,
    ): WebSocket = streamingClient.newWebSocket(
        Request.Builder()
            .url(webSocketUrl("/api/v1/meetings/$meetingId/stream"))
            .apply { if (token != null) header("Authorization", "Bearer $token") }
            .build(),
        listener,
    )

    internal fun webSocketUrl(path: String): String {
        val wsRoot = when {
            root.startsWith("https://") -> "wss://" + root.removePrefix("https://")
            root.startsWith("http://") -> "ws://" + root.removePrefix("http://")
            else -> root
        }
        return wsRoot + path
    }

    suspend fun uploadAudio(
        meetingId: String,
        file: File,
        filename: String = file.name,
    ): Meeting {
        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart(
                "file",
                filename,
                file.asRequestBody(AUDIO_WAV.toMediaType()),
            )
            .build()
        val request = request("/api/v1/meetings/$meetingId/audio")
            .post(body)
            .build()
        return decode(execute(request))
    }

    /**
     * Requests processing. With the backend's inline job backend this call
     * blocks until transcription finishes, so it uses a long timeout; with a
     * worker it returns 202 quickly and the caller polls [meeting].
     */
    suspend fun transcribe(meetingId: String): Meeting {
        val request = request("/api/v1/meetings/$meetingId/transcribe")
            .post(EMPTY_BODY)
            .build()
        return decode(execute(request, longClient))
    }

    suspend fun meeting(meetingId: String): Meeting =
        getJson("/api/v1/meetings/$meetingId")

    suspend fun transcript(meetingId: String): Transcript =
        getJson("/api/v1/meetings/$meetingId/transcript")

    suspend fun minutes(meetingId: String): Minutes =
        getJson("/api/v1/meetings/$meetingId/minutes")

    suspend fun deleteMeeting(meetingId: String) {
        execute(request("/api/v1/meetings/$meetingId").delete().build())
    }

    suspend fun logout() {
        execute(request("/api/v1/auth/logout").post(EMPTY_BODY).build())
    }

    // -- assistant -------------------------------------------------------

    /**
     * Sends one turn. A null [conversationId] starts a new conversation and the
     * reply carries the id to continue it.
     */
    suspend fun assistantChat(
        message: String,
        conversationId: String?,
        input: String = "text",
    ): AssistantChatReply {
        val body = json.encodeToString(
            AssistantChatRequest(message, conversationId, channel = "api", input = input),
        )
        return decode(
            execute(
                request("/api/v1/assistant/chat")
                    .post(body.toRequestBody(JSON_MEDIA_TYPE))
                    .build(),
                chatClient,
            ),
        )
    }

    suspend fun assistantConversations(limit: Int = 50): AssistantConversationList =
        getJson("/api/v1/assistant/conversations?limit=${limit.coerceIn(1, 200)}")

    suspend fun assistantConversation(conversationId: String): AssistantConversation =
        getJson("/api/v1/assistant/conversations/${encodePath(conversationId)}")

    /**
     * Uploads one recorded utterance and returns its text.
     *
     * This is the paid step of a voice turn: the server sends the audio to the
     * ASR bound to `meetings.transcribe`. The wake word itself never leaves the
     * phone. The chat client's longer timeout is used because a cold vendor
     * call plus a short upload can exceed the default read timeout.
     */
    suspend fun transcribeVoice(audio: File): VoiceTranscript {
        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("file", audio.name, audio.asRequestBody(WAV_MEDIA_TYPE))
            .build()
        return decode(
            execute(
                request("/api/v1/assistant/transcribe").post(body).build(),
                chatClient,
            ),
        )
    }

    // -- phone data ------------------------------------------------------

    suspend fun registerPhoneDevice(payload: PhoneDevicePayload): PhoneDevice =
        postJson("/api/v1/phone/devices", json.encodeToString(payload))

    suspend fun listPhoneDevices(): List<PhoneDevice> =
        getJson("/api/v1/phone/devices")

    suspend fun phoneDevice(deviceId: String): PhoneDevice =
        getJson("/api/v1/phone/devices/$deviceId")

    suspend fun syncPhone(deviceId: String, request: PhoneSyncRequest): PhoneSyncResult =
        decode(
            execute(
                request("/api/v1/phone/devices/$deviceId/sync")
                    .post(json.encodeToString(request).toRequestBody(JSON_MEDIA_TYPE))
                    .build(),
            ),
        )

    suspend fun phoneDeviceIds(
        deviceId: String,
        kind: String,
        since: String? = null,
        until: String? = null,
    ): List<String> {
        val query = buildString {
            append("?kind=").append(URLEncoder.encode(kind, "UTF-8"))
            if (since != null) append("&since=").append(URLEncoder.encode(since, "UTF-8"))
            if (until != null) append("&until=").append(URLEncoder.encode(until, "UTF-8"))
        }
        return getJson<PhoneDeviceIds>(
            "/api/v1/phone/devices/$deviceId/ids$query"
        ).externalIds
    }

    suspend fun pendingPhoneCommands(deviceId: String): List<PhoneCommand> =
        getJson("/api/v1/phone/devices/$deviceId/commands")

    suspend fun reportPhoneCommand(
        deviceId: String,
        commandId: String,
        result: PhoneCommandResult,
    ): PhoneCommand = postJson(
        "/api/v1/phone/devices/$deviceId/commands/$commandId/result",
        json.encodeToString(result),
    )

    suspend fun phoneSummary(): PhoneSummary = getJson("/api/v1/phone/summary")

    suspend fun wipePhoneDevice(deviceId: String) {
        execute(request("/api/v1/phone/devices/$deviceId/data").delete().build())
    }

    // -- push ------------------------------------------------------------

    /**
     * Opens the background notification stream. The bearer header is set on the
     * handshake; the server replays events after [afterSeq] and then streams
     * live ones. The foreground service owns reconnects.
     */
    fun openPushStream(
        deviceId: String?,
        afterSeq: Long,
        listener: WebSocketListener,
    ): WebSocket {
        val query = buildString {
            append("?after=").append(afterSeq.coerceAtLeast(0))
            if (!deviceId.isNullOrBlank()) {
                append("&device_id=").append(URLEncoder.encode(deviceId, "UTF-8"))
            }
        }
        return streamingClient.newWebSocket(
            Request.Builder()
                .url(webSocketUrl("/api/v1/push/ws$query"))
                .apply { if (token != null) header("Authorization", "Bearer $token") }
                .build(),
            listener,
        )
    }

    suspend fun pushBacklog(
        afterSeq: Long,
        deviceId: String? = null,
        limit: Int = 100,
    ): PushList {
        val query = buildString {
            append("?after_seq=").append(afterSeq.coerceAtLeast(0))
            append("&limit=").append(limit.coerceIn(1, 500))
            if (!deviceId.isNullOrBlank()) {
                append("&device_id=").append(URLEncoder.encode(deviceId, "UTF-8"))
            }
        }
        return getJson("/api/v1/push/notifications$query")
    }

    suspend fun pushStatus(): PushStatus = getJson("/api/v1/push/status")

    suspend fun sendTestPush(
        title: String = "Test notification",
        body: String = "If you can see this, the background connection works.",
    ): PushEvent = postJson(
        "/api/v1/push/test",
        json.encodeToString(PushTestRequest(title, body)),
    )

    private suspend inline fun <reified T> getJson(path: String): T =
        decode(execute(request(path).get().build()))

    /**
     * Acknowledge one proactive item, from the notification action.
     *
     * The item id comes from the push payload; the token is the stored account
     * session, so the receiver works without the UI process.
     */
    suspend fun ackProactive(itemId: String) {
        postUnit(
            "/api/v1/proactive/items/${encodePath(itemId)}/ack",
            """{"via":"app"}""",
        )
    }

    suspend fun snoozeProactive(itemId: String, minutes: Int = 60) {
        postUnit(
            "/api/v1/proactive/items/${encodePath(itemId)}/snooze",
            """{"minutes":$minutes}""",
        )
    }

    private suspend fun postUnit(path: String, body: String = "{}") {
        execute(request(path).post(body.toRequestBody(JSON_MEDIA_TYPE)).build())
    }

    private suspend inline fun <reified T> postJson(path: String, body: String): T =
        decode(
            execute(
                request(path).post(body.toRequestBody(JSON_MEDIA_TYPE)).build(),
            ),
        )

    private fun request(path: String): Request.Builder =
        Request.Builder()
            .url(root + path)
            .apply { if (token != null) header("Authorization", "Bearer $token") }

    private suspend fun execute(
        request: Request,
        http: OkHttpClient = client,
    ): String = withContext(Dispatchers.IO) {
        http.newCall(request).execute().use { response ->
            val text = response.body.string()
            if (!response.isSuccessful) {
                val detail = errorDetail(text)
                if (response.code == 401) {
                    throw UnauthorizedException(detail)
                }
                throw ApiException(response.code, detail)
            }
            text
        }
    }

    private inline fun <reified T> decode(text: String): T = json.decodeFromString(text)

    /** Encodes one path segment; a `+` would mean a space in a query, not a path. */
    private fun encodePath(value: String): String =
        URLEncoder.encode(value, "UTF-8").replace("+", "%20")

    private fun errorDetail(body: String): String {
        if (body.isBlank()) return "request failed"
        return runCatching {
            json.parseToJsonElement(body).jsonObject["detail"]?.jsonPrimitive?.content
        }.getOrNull() ?: body.take(300)
    }

    companion object {
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
        private val EMPTY_BODY: RequestBody = ByteArray(0).toRequestBody(null)
        private const val AUDIO_WAV = "audio/wav"

        private val WAV_MEDIA_TYPE = "audio/wav".toMediaType()

        val json: Json = Json {
            ignoreUnknownKeys = true
            explicitNulls = false
        }
    }
}

private val defaultHttpClient: OkHttpClient = OkHttpClient.Builder()
    .connectTimeout(15, TimeUnit.SECONDS)
    .readTimeout(60, TimeUnit.SECONDS)
    .writeTimeout(120, TimeUnit.SECONDS)
    .build()

@Serializable
private data class LoginRequest(val username: String, val password: String)

@Serializable
private data class RegisterRequest(val username: String, val password: String)

@Serializable
private data class MeetingCreate(
    val title: String,
    @kotlinx.serialization.SerialName("local_language") val localLanguage: String,
    val source: String,
)
