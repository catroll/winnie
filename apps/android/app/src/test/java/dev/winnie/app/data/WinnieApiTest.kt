package dev.winnie.app.data

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.io.File
import java.net.InetSocketAddress
import java.util.concurrent.Executors
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.assertThrows
import org.junit.Before
import org.junit.Test

class WinnieApiTest {

    private lateinit var server: HttpServer
    private val received = mutableListOf<ReceivedRequest>()
    private var respond: (ReceivedRequest) -> StubResponse = {
        StubResponse(200, "{}")
    }

    @Before
    fun setUp() {
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") { exchange -> handle(exchange) }
        server.executor = Executors.newCachedThreadPool()
        server.start()
    }

    @After
    fun tearDown() {
        server.stop(0)
    }

    @Test
    fun loginSendsCredentialsAndParsesToken() = runBlocking {
        respond = {
            StubResponse(
                200,
                """
                {
                  "access_token": "token-123",
                  "token_type": "bearer",
                  "expires_at": "2026-11-30T00:00:00Z",
                  "user": {
                    "id": "u1",
                    "username": "alice",
                    "display_name": null,
                    "created_at": "2026-10-31T00:00:00"
                  }
                }
                """.trimIndent(),
            )
        }

        val token = api().login("alice", "secret")

        assertEquals("token-123", token.accessToken)
        assertEquals("alice", token.user.username)
        val request = received.single()
        assertEquals("POST", request.method)
        assertEquals("/api/v1/auth/login", request.path)
        assertTrue(request.body.contains("\"username\":\"alice\""))
        assertTrue(request.body.contains("\"password\":\"secret\""))
    }

    @Test
    fun authenticatedRequestsCarryBearerToken() = runBlocking {
        respond = {
            StubResponse(
                200,
                """
                {
                  "items": [
                    {
                      "id": "m1",
                      "title": "Weekly sync",
                      "status": "ready",
                      "stage": null,
                      "source": "file",
                      "local_language": "zh",
                      "audio_duration_ms": 1200,
                      "error": null,
                      "created_at": "2026-10-31T01:00:00",
                      "updated_at": "2026-10-31T01:05:00"
                    }
                  ],
                  "total": 1
                }
                """.trimIndent(),
            )
        }

        val meetings = api(token = "abc").listMeetings()

        assertEquals(1, meetings.total)
        assertEquals("Weekly sync", meetings.items.single().title)
        assertEquals("Bearer abc", received.single().headers["Authorization"])
    }

    @Test
    fun unauthorizedResponseIsTypedAndKeepsServerDetail() {
        respond = {
            StubResponse(401, """{"detail":"invalid username or password"}""")
        }

        val error = assertThrows(UnauthorizedException::class.java) {
            runBlocking { api().me() }
        }
        assertEquals("invalid username or password", error.message)
    }

    @Test
    fun nonJsonErrorBodyIsTruncatedNotDropped() {
        respond = { StubResponse(502, "upstream exploded") }

        val error = assertThrows(ApiException::class.java) {
            runBlocking { api().me() }
        }
        assertEquals(502, error.statusCode)
        assertEquals("upstream exploded", error.message)
    }

    @Test
    fun uploadSendsMultipartWav() = runBlocking {
        respond = {
            StubResponse(
                200,
                """
                {
                  "id": "m1", "title": "Upload", "status": "uploaded",
                  "stage": "upload", "source": "file", "local_language": "zh",
                  "audio_duration_ms": null, "error": null,
                  "created_at": "2026-10-31T01:00:00",
                  "updated_at": "2026-10-31T01:01:00"
                }
                """.trimIndent(),
            )
        }
        val file = File.createTempFile("winnie-upload", ".wav")
        file.writeBytes("RIFF-test-payload".toByteArray())

        try {
            val meeting = api(token = "abc").uploadAudio("m1", file, "recording.wav")

            assertEquals("uploaded", meeting.status)
            val request = received.single()
            assertEquals("POST", request.method)
            assertEquals("/api/v1/meetings/m1/audio", request.path)
            assertTrue(request.contentType.startsWith("multipart/form-data"))
            assertTrue(request.body.contains("filename=\"recording.wav\""))
            assertTrue(request.body.contains("Content-Type: audio/wav"))
            assertTrue(request.body.contains("RIFF-test-payload"))
        } finally {
            file.delete()
        }
    }

    @Test
    fun transcriptKeepsTranslationAndLanguage() = runBlocking {
        respond = {
            StubResponse(
                200,
                """
                {
                  "meeting_id": "m1",
                  "segments": [
                    {
                      "seq": 0,
                      "start_ms": 0,
                      "end_ms": 3000,
                      "speaker": null,
                      "text": "大家好",
                      "detected_language": "zh",
                      "translated_text": "Hello everyone",
                      "translation_status": "done"
                    }
                  ]
                }
                """.trimIndent(),
            )
        }

        val transcript = api(token = "abc").transcript("m1")

        val segment = transcript.segments.single()
        assertEquals("大家好", segment.text)
        assertEquals("Hello everyone", segment.translatedText)
        assertEquals("zh", segment.detectedLanguage)
    }

    @Test
    fun createMeetingCanMarkTheSourceRealtime() = runBlocking {
        respond = {
            StubResponse(
                201,
                """
                {
                  "id": "m1", "title": "Live", "status": "created",
                  "stage": null, "source": "realtime", "local_language": "zh",
                  "audio_duration_ms": null, "error": null,
                  "created_at": "2026-10-31T01:00:00",
                  "updated_at": "2026-10-31T01:00:00"
                }
                """.trimIndent(),
            )
        }

        val meeting = api(token = "abc").createMeeting(
            title = "Live",
            localLanguage = "zh",
            source = "realtime",
        )

        assertEquals("realtime", meeting.source)
        assertTrue(received.single().body.contains("\"source\":\"realtime\""))
    }

    @Test
    fun websocketUrlUsesTheMatchingScheme() {
        assertEquals(
            "ws://192.168.1.38:8000/api/v1/meetings/m1/stream",
            WinnieApi("http://192.168.1.38:8000")
                .webSocketUrl("/api/v1/meetings/m1/stream"),
        )
        assertEquals(
            "wss://winnie.example/api/v1/meetings/m1/stream",
            WinnieApi("https://winnie.example")
                .webSocketUrl("/api/v1/meetings/m1/stream"),
        )
    }

    @Test
    fun assistantChatContinuesTheConversationAndReportsTools() = runBlocking {
        respond = {
            StubResponse(
                200,
                """
                {
                  "conversation_id": "20261101T021500Z-3f9a",
                  "reply": "The weather is mild.",
                  "tools_used": ["weather"],
                  "usage_tokens": 42.5
                }
                """.trimIndent(),
            )
        }

        val reply = api(token = "abc").assistantChat(
            message = "What is the weather?",
            conversationId = "20261101T021500Z-3f9a",
        )

        assertEquals("20261101T021500Z-3f9a", reply.conversationId)
        assertEquals("The weather is mild.", reply.reply)
        assertEquals(listOf("weather"), reply.toolsUsed)
        val request = received.single()
        assertEquals("POST", request.method)
        assertEquals("/api/v1/assistant/chat", request.path)
        assertTrue(request.body.contains("\"channel\":\"api\""))
        assertTrue(
            request.body.contains("\"conversation_id\":\"20261101T021500Z-3f9a\""),
        )
    }

    @Test
    fun aNewAssistantChatOmitsTheConversationId() = runBlocking {
        respond = {
            StubResponse(
                200,
                """{"conversation_id":"c2","reply":"Hi","tools_used":[]}""",
            )
        }

        api(token = "abc").assistantChat(message = "Hello", conversationId = null)

        // explicitNulls = false: the server must see no conversation_id at all,
        // not a JSON null it would have to special-case.
        assertTrue(!received.single().body.contains("conversation_id"))
    }

    @Test
    fun assistantConversationKeepsToolMessages() = runBlocking {
        respond = {
            StubResponse(
                200,
                """
                {
                  "id": "c1",
                  "title": "Schedule",
                  "created_at": "2026-11-01T02:15:00",
                  "source": "user",
                  "channel": "api",
                  "messages": [
                    {"role": "user", "name": "", "text": "What is today?", "at": "2026-11-01T02:15:00"},
                    {"role": "tool", "name": "weather", "text": "sunny", "at": "2026-11-01T02:15:01"},
                    {"role": "assistant", "name": "", "text": "**Sunny.**", "at": "2026-11-01T02:15:02"}
                  ]
                }
                """.trimIndent(),
            )
        }

        val conversation = api(token = "abc").assistantConversation("c1")

        assertEquals("Schedule", conversation.title)
        assertEquals(3, conversation.messages.size)
        assertEquals("weather", conversation.messages[1].name)
        assertEquals("**Sunny.**", conversation.messages[2].text)
        assertEquals("/api/v1/assistant/conversations/c1", received.single().path)
    }

    private fun api(token: String? = null): WinnieApi =
        WinnieApi("http://127.0.0.1:${server.address.port}", token)

    private fun handle(exchange: HttpExchange) {
        val body = exchange.requestBody.readBytes().toString(Charsets.ISO_8859_1)
        val request = ReceivedRequest(
            method = exchange.requestMethod,
            path = exchange.requestURI.path,
            headers = exchange.requestHeaders.entries.associate {
                it.key to it.value.joinToString(",")
            },
            contentType = exchange.requestHeaders.getFirst("Content-Type").orEmpty(),
            body = body,
        )
        received += request
        val response = respond(request)
        val bytes = response.body.toByteArray()
        exchange.responseHeaders.add("Content-Type", "application/json")
        val length = if (bytes.isEmpty()) -1L else bytes.size.toLong()
        exchange.sendResponseHeaders(response.status, length)
        exchange.responseBody.use { if (bytes.isNotEmpty()) it.write(bytes) }
    }

    private data class ReceivedRequest(
        val method: String,
        val path: String,
        val headers: Map<String, String>,
        val contentType: String,
        val body: String,
    )

    private data class StubResponse(val status: Int, val body: String)
}
