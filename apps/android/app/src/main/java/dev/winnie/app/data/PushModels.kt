package dev.winnie.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * One frame from `/api/v1/push/ws`.
 *
 * The server sends `{"type":"event", ...}` for a notification, `"ready"` once
 * the backlog is drained, and `"ping"` as a heartbeat. The client ignores
 * unknown fields, so the server can add metadata without breaking older APKs.
 */
@Serializable
data class PushEvent(
    val type: String = "event",
    @SerialName("seq") val seq: Long = 0,
    val id: String = "",
    val kind: String = "",
    val title: String = "",
    val body: String = "",
    val data: JsonObject = JsonObject(emptyMap()),
    @SerialName("device_id") val deviceId: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("expires_at") val expiresAt: String? = null,
)

@Serializable
data class PushList(
    val events: List<PushEvent> = emptyList(),
    @SerialName("last_seq") val lastSeq: Long = 0,
)

@Serializable
data class PushStatus(
    val enabled: Boolean = false,
    val connected: Int = 0,
    val pending: Int = 0,
    @SerialName("last_event_at") val lastEventAt: String? = null,
    @SerialName("last_seq") val lastSeq: Long = 0,
)

@Serializable
data class PushTestRequest(
    val title: String = "Test notification",
    val body: String = "If you can see this, the background connection works.",
)
