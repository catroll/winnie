package dev.winnie.app.data

import kotlinx.serialization.decodeFromString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The frame contract between the server and the background service. */
class PushModelsTest {

    private val json = WinnieApi.json

    @Test
    fun parsesANotificationEvent() {
        val event = json.decodeFromString<PushEvent>(
            """
            {"type":"event","seq":7,"id":"11111111-1111-1111-1111-111111111111",
             "kind":"notification","title":"Standup","body":"In 10 minutes",
             "data":{"task_id":"t1"},"device_id":null,
             "created_at":"2026-11-01T05:00:00Z","expires_at":null}
            """.trimIndent()
        )

        assertEquals("event", event.type)
        assertEquals(7L, event.seq)
        assertEquals("notification", event.kind)
        assertEquals("Standup", event.title)
        assertEquals("In 10 minutes", event.body)
        assertEquals("t1", event.data["task_id"].toString().trim('"'))
        assertNull(event.deviceId)
    }

    @Test
    fun parsesTheReadyAndPingFrames() {
        val ready = json.decodeFromString<PushEvent>(
            """{"type":"ready","last_seq":9}"""
        )
        val ping = json.decodeFromString<PushEvent>("""{"type":"ping"}""")

        assertEquals("ready", ready.type)
        assertEquals("ping", ping.type)
    }

    @Test
    fun ignoresFieldsThisVersionDoesNotKnow() {
        val event = json.decodeFromString<PushEvent>(
            """{"type":"event","seq":1,"title":"T","future_field":{"a":1}}"""
        )

        assertEquals(1L, event.seq)
        assertEquals("T", event.title)
    }

    @Test
    fun parsesTheBacklogAndStatus() {
        val list = json.decodeFromString<PushList>(
            """
            {"events":[{"seq":3,"id":"11111111-1111-1111-1111-111111111111",
              "kind":"sync","title":"","body":""}],"last_seq":3}
            """.trimIndent()
        )
        val status = json.decodeFromString<PushStatus>(
            """{"enabled":true,"connected":1,"pending":4,"last_event_at":null,"last_seq":12}"""
        )

        assertEquals(1, list.events.size)
        assertEquals(3L, list.lastSeq)
        assertEquals(4, status.pending)
        assertEquals(12L, status.lastSeq)
    }
}
