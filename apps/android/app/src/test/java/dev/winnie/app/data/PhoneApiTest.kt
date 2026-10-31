package dev.winnie.app.data

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.util.concurrent.Executors
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.encodeToJsonElement
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** Wire contract checks for the phone endpoints, against a real HTTP server. */
class PhoneApiTest {

    private lateinit var server: HttpServer
    private val requests = mutableListOf<Captured>()
    private var respond: (Captured) -> String = { "{}" }
    private var status = 200

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
    fun syncPostsTheTypedBatch() = runBlocking {
        status = 200
        respond = {
            """
            {"kind":"message","accepted":1,"deleted":0,"cursor":"11",
             "server_time":"2026-11-01T06:00:00Z","interval_minutes":30}
            """.trimIndent()
        }
        val item = PhoneMessageItem(
            externalId = "11",
            threadId = "3",
            address = "+8613800138000",
            direction = "inbox",
            body = "hello",
            receivedAt = "2026-11-01T05:59:00Z",
            isRead = false,
        )
        val request = PhoneSyncRequest(
            kind = "message",
            mode = "incremental",
            cursor = "10",
            items = JsonArray(
                listOf(WinnieApi.json.encodeToJsonElement(PhoneMessageItem.serializer(), item))
            ),
        )

        val result = api().syncPhone("dev-1", request)

        assertEquals(1, result.accepted)
        assertEquals("11", result.cursor)
        assertEquals(30, result.intervalMinutes)

        val captured = requests.single()
        assertEquals("POST", captured.method)
        assertEquals("/api/v1/phone/devices/dev-1/sync", captured.path)
        assertEquals("Bearer token", captured.authorization)
        assertTrue(captured.body.contains("\"kind\":\"message\""))
        assertTrue(captured.body.contains("\"external_id\":\"11\""))
        assertTrue(captured.body.contains("\"cursor\":\"10\""))
        assertTrue(captured.body.contains("\"is_read\":false"))
    }

    @Test
    fun pendingCommandsParsePayloadAndStatus() = runBlocking {
        respond = {
            """
            [{"id":"c1","device_id":"d1","kind":"sms.send",
              "payload":{"address":"+861", "body":"hi"},
              "summary":"send SMS","status":"dispatched","result":"","error":"",
              "expires_at":null,"approved_at":null,"completed_at":null,
              "created_at":"2026-11-01T05:00:00Z"}]
            """.trimIndent()
        }

        val commands = api().pendingPhoneCommands("d1")

        assertEquals(1, commands.size)
        assertEquals("sms.send", commands[0].kind)
        assertEquals("dispatched", commands[0].status)
        assertEquals("+861", commands[0].payload["address"].toString().trim('"'))
        assertEquals("/api/v1/phone/devices/d1/commands", requests.single().path)
    }

    @Test
    fun reportSendsStatusAndBody() = runBlocking {
        respond = {
            """
            {"id":"c1","device_id":"d1","kind":"sms.send","payload":{},
             "summary":"send SMS","status":"succeeded","result":"ok","error":"",
             "expires_at":null,"approved_at":null,"completed_at":null,
             "created_at":"2026-11-01T05:00:00Z"}
            """.trimIndent()
        }

        val updated = api().reportPhoneCommand(
            "d1",
            "c1",
            PhoneCommandResult(status = "succeeded", result = "ok"),
        )

        assertEquals("succeeded", updated.status)
        assertEquals("/api/v1/phone/devices/d1/commands/c1/result", requests.single().path)
        assertTrue(requests.single().body.contains("\"status\":\"succeeded\""))
    }

    @Test
    fun idsEncodeTheWindow() = runBlocking {
        respond = { """{"kind":"calendar","external_ids":["1","2"]}""" }

        val ids = api().phoneDeviceIds(
            "d1",
            "calendar",
            since = "2026-11-01T00:00:00+08:00",
            until = "2026-12-01T00:00:00+08:00",
        )

        assertEquals(listOf("1", "2"), ids)
        val path = requests.single().path
        assertTrue(path.startsWith("/api/v1/phone/devices/d1/ids?"))
        // A raw + in a query string decodes as a space, so it must be escaped.
        assertTrue(path.contains("since=2026-11-01T00%3A00%3A00%2B08%3A00"))
    }

    @Test
    fun summaryParsesCountsAndStaleness() = runBlocking {
        respond = {
            """
            {"devices":[{"device":{"id":"d1","device_key":"k","name":"Pixel 8",
              "platform":"android","manufacturer":"Google","model":"Pixel 8",
              "os_version":"17","app_version":"0.1.0","timezone":"Asia/Shanghai",
              "scopes":["message"],"info":{},"revoked":false,
              "last_seen_at":null,"last_sync_at":null,"created_at":"",
              "states":[]},"counts":[{"kind":"message","count":7}]}],
             "counts":[{"kind":"message","count":7}],
             "last_sync_at":null,"stale_minutes":12,"pending_commands":2}
            """.trimIndent()
        }

        val summary = api().phoneSummary()

        assertEquals("Pixel 8", summary.devices.single().device.name)
        assertEquals(7, summary.counts.single().count)
        assertEquals(12, summary.staleMinutes)
        assertEquals(2, summary.pendingCommands)
    }

    @Test
    fun wipeUsesDelete() = runBlocking {
        respond = { """{"removed":12}""" }

        api().wipePhoneDevice("d1")

        assertEquals("DELETE", requests.single().method)
        assertEquals("/api/v1/phone/devices/d1/data", requests.single().path)
    }

    @Test
    fun registerPostsTheDeviceProfile() = runBlocking {
        respond = {
            """
            {"id":"d1","device_key":"key-1","name":"Pixel 8","platform":"android",
             "manufacturer":"Google","model":"Pixel 8","os_version":"17",
             "app_version":"0.1.0","timezone":"Asia/Shanghai","scopes":["message"],
             "info":{},"revoked":false,"last_seen_at":null,"last_sync_at":null,
             "created_at":"2026-11-01T05:00:00Z","states":[]}
            """.trimIndent()
        }

        val device = api().registerPhoneDevice(
            PhoneDevicePayload(
                deviceKey = "key-1",
                name = "Pixel 8",
                scopes = listOf("message"),
            )
        )

        assertEquals("d1", device.id)
        assertEquals("/api/v1/phone/devices", requests.single().path)
        assertTrue(requests.single().body.contains("\"device_key\":\"key-1\""))
        assertTrue(requests.single().body.contains("\"scopes\":[\"message\"]"))
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
        exchange.sendResponseHeaders(status, payload.size.toLong())
        exchange.responseBody.use { it.write(payload) }
    }

    private data class Captured(
        val method: String,
        val path: String,
        val body: String,
        val authorization: String,
    )
}
