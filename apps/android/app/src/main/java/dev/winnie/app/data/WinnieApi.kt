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

    private suspend inline fun <reified T> getJson(path: String): T =
        decode(execute(request(path).get().build()))

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
