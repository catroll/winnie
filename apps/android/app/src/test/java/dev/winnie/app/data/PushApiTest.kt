package dev.winnie.app.data

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.util.concurrent.Executors
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** Wire checks for the REST side of the push contract. */
class PushApiTest {

    private lateinit var server: HttpServer
    private val requests = mutableListOf<Captured>()
    private var respond: (Captured) -> String = { "{}" }

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
    fun backlogSendsTheCursorAndParsesEvents() = runBlocking {
        respond = {
            """
            {"events":[{"seq":6,"id":"11111111-1111-1111-1111-111111111111",
              "kind":"notification","title":"Hello","body":"World"}],
             "last_seq":6}
            """.trimIndent()
        }

        val list = api().pushBacklog(afterSeq = 5, deviceId = "dev-1", limit = 50)

        assertEquals(6L, list.lastSeq)
        assertEquals("Hello", list.events.single().title)
        val path = requests.single().path
        assertTrue(path.startsWith("/api/v1/push/notifications?"))
        assertTrue(path.contains("after_seq=5"))
        assertTrue(path.contains("limit=50"))
        assertTrue(path.contains("device_id=dev-1"))
        assertEquals("Bearer token", requests.single().authorization)
    }

    @Test
    fun statusParsesCounts() = runBlocking {
        respond = { """{"enabled":true,"connected":1,"pending":3,"last_event_at":null}""" }

        val status = api().pushStatus()

        assertEquals(1, status.connected)
        assertEquals(3, status.pending)
        assertEquals("/api/v1/push/status", requests.single().path)
    }

    @Test
    fun testNotificationPostsTheBody() = runBlocking {
        respond = {
            """
            {"seq":9,"id":"11111111-1111-1111-1111-111111111111",
             "kind":"notification","title":"Ping","body":"Now"}
            """.trimIndent()
        }

        val event = api().sendTestPush(title = "Ping", body = "Now")

        assertEquals("Ping", event.title)
        assertEquals("POST", requests.single().method)
        assertEquals("/api/v1/push/test", requests.single().path)
        assertTrue(requests.single().body.contains("\"title\":\"Ping\""))
        assertTrue(requests.single().body.contains("\"body\":\"Now\""))
    }

    private fun api() = WinnieApi("http://127.0.0.1:${server.address.port}", "token")

    private fun handle(exchange: HttpExchange) {
        val body = exchange.requestBody.readBytes().toString(Charsets.UTF_8)
        val captured = Captured(
            method = exchange.requestMethod,
            path = exchange.requestURI.toString(),
            body = body,
            authorization = exchange.requestHeaders.getFirst("Authorization").orEmpty(),
        )
        requests += captured
        val payload = respond(captured).toByteArray()
        exchange.responseHeaders.add("Content-Type", "application/json")
        exchange.sendResponseHeaders(200, payload.size.toLong())
        exchange.responseBody.use { it.write(payload) }
    }

    private data class Captured(
        val method: String,
        val path: String,
        val body: String,
        val authorization: String,
    )
}
