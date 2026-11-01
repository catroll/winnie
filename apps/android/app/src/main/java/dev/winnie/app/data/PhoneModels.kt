package dev.winnie.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject

/**
 * Wire models for `/api/v1/phone`.
 *
 * The upload kinds mirror the server's per-kind item models exactly; the
 * server rejects unknown fields, so a rename here fails at the edge rather
 * than being silently ignored. Times are ISO-8601 strings with an offset.
 */

@Serializable
data class PhoneCalendarItem(
    @SerialName("external_id") val externalId: String,
    @SerialName("calendar_id") val calendarId: String = "",
    @SerialName("calendar_name") val calendarName: String = "",
    val title: String = "",
    val description: String = "",
    val location: String = "",
    @SerialName("starts_at") val startsAt: String? = null,
    @SerialName("ends_at") val endsAt: String? = null,
    @SerialName("all_day") val allDay: Boolean = false,
    val timezone: String = "",
    val recurrence: String = "",
    val exdate: String = "",
    val organizer: String = "",
    val status: String = "",
    @SerialName("updated_at") val updatedAt: String? = null,
)

@Serializable
data class PhoneCallItem(
    @SerialName("external_id") val externalId: String,
    val number: String = "",
    @SerialName("cached_name") val cachedName: String = "",
    val direction: String = "unknown",
    @SerialName("started_at") val startedAt: String? = null,
    @SerialName("duration_seconds") val durationSeconds: Int = 0,
    @SerialName("sim_account") val simAccount: String = "",
    @SerialName("is_new") val isNew: Boolean = false,
)

@Serializable
data class PhoneMessageItem(
    @SerialName("external_id") val externalId: String,
    @SerialName("thread_id") val threadId: String = "",
    val address: String = "",
    val direction: String = "inbox",
    val body: String = "",
    val subject: String = "",
    @SerialName("received_at") val receivedAt: String? = null,
    @SerialName("is_read") val isRead: Boolean = true,
    val kind: String = "sms",
)

@Serializable
data class PhoneContactBirthday(
    val month: Int,
    val day: Int,
    // Null when the provider stored `--MM-DD`: the owner never entered a year.
    val year: Int? = null,
    val type: String = "birthday",
)

@Serializable
data class PhoneContactItem(
    @SerialName("external_id") val externalId: String,
    @SerialName("display_name") val displayName: String = "",
    val numbers: List<String> = emptyList(),
    val emails: List<String> = emptyList(),
    val organization: String = "",
    val starred: Boolean = false,
    val birthdays: List<PhoneContactBirthday> = emptyList(),
)

@Serializable
data class PhoneNotificationItem(
    @SerialName("external_id") val externalId: String,
    @SerialName("package_name") val packageName: String = "",
    @SerialName("app_label") val appLabel: String = "",
    val title: String = "",
    val text: String = "",
    val category: String = "",
    @SerialName("posted_at") val postedAt: String? = null,
    @SerialName("is_ongoing") val isOngoing: Boolean = false,
    @SerialName("is_clearable") val isClearable: Boolean = true,
)

@Serializable
data class PhoneLocationItem(
    @SerialName("external_id") val externalId: String,
    val latitude: Double,
    val longitude: Double,
    @SerialName("accuracy_m") val accuracyM: Double? = null,
    @SerialName("altitude_m") val altitudeM: Double? = null,
    @SerialName("speed_mps") val speedMps: Double? = null,
    val provider: String = "",
    @SerialName("captured_at") val capturedAt: String? = null,
)

@Serializable
data class PhoneAppItem(
    @SerialName("external_id") val externalId: String,
    val label: String = "",
    @SerialName("package_name") val packageName: String = "",
    @SerialName("version_name") val versionName: String = "",
    @SerialName("version_code") val versionCode: Long = 0,
    @SerialName("is_system") val isSystem: Boolean = false,
    @SerialName("is_enabled") val isEnabled: Boolean = true,
    @SerialName("installed_at") val installedAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

/**
 * One sync batch. `items` is a JSON array because the server validates the
 * item shape per `kind`; the client encodes each source's typed items into it
 * with their own serializer.
 */
@Serializable
data class PhoneSyncRequest(
    val kind: String,
    val mode: String = "incremental",
    val cursor: String = "",
    val items: JsonArray = JsonArray(emptyList()),
    val deleted: List<String> = emptyList(),
    // A full snapshot may cover only a window (calendar). The server then
    // diffs deletions inside that window and leaves older rows alone.
    val since: String? = null,
    val until: String? = null,
    val device: PhoneDeviceSnapshot? = null,
)

@Serializable
data class PhoneDeviceSnapshot(
    val name: String = "",
    val manufacturer: String = "",
    val model: String = "",
    @SerialName("os_version") val osVersion: String = "",
    @SerialName("app_version") val appVersion: String = "",
    val timezone: String = "",
    val scopes: List<String> = emptyList(),
    val info: JsonObject = JsonObject(emptyMap()),
)

@Serializable
data class PhoneDevicePayload(
    @SerialName("device_key") val deviceKey: String,
    val name: String = "",
    val platform: String = "android",
    val manufacturer: String = "",
    val model: String = "",
    @SerialName("os_version") val osVersion: String = "",
    @SerialName("app_version") val appVersion: String = "",
    val timezone: String = "",
    val scopes: List<String> = emptyList(),
    val info: JsonObject = JsonObject(emptyMap()),
)

@Serializable
data class PhoneSyncState(
    val kind: String,
    val cursor: String = "",
    @SerialName("last_sync_at") val lastSyncAt: String? = null,
    @SerialName("full_sync_at") val fullSyncAt: String? = null,
    @SerialName("last_error") val lastError: String = "",
    @SerialName("accepted_total") val acceptedTotal: Int = 0,
)

@Serializable
data class PhoneDevice(
    val id: String,
    @SerialName("device_key") val deviceKey: String = "",
    val name: String = "",
    val platform: String = "android",
    val manufacturer: String = "",
    val model: String = "",
    @SerialName("os_version") val osVersion: String = "",
    @SerialName("app_version") val appVersion: String = "",
    val timezone: String = "",
    val scopes: List<String> = emptyList(),
    val info: JsonObject = JsonObject(emptyMap()),
    val revoked: Boolean = false,
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
    @SerialName("last_sync_at") val lastSyncAt: String? = null,
    @SerialName("created_at") val createdAt: String = "",
    val states: List<PhoneSyncState> = emptyList(),
)

@Serializable
data class PhoneSyncResult(
    val kind: String,
    val accepted: Int = 0,
    val deleted: Int = 0,
    val cursor: String = "",
    @SerialName("server_time") val serverTime: String = "",
    @SerialName("interval_minutes") val intervalMinutes: Int = 30,
)

@Serializable
data class PhoneKindCount(
    val kind: String,
    val count: Int = 0,
)

@Serializable
data class PhoneDeviceStatus(
    val device: PhoneDevice,
    val counts: List<PhoneKindCount> = emptyList(),
)

@Serializable
data class PhoneSummary(
    val devices: List<PhoneDeviceStatus> = emptyList(),
    val counts: List<PhoneKindCount> = emptyList(),
    @SerialName("last_sync_at") val lastSyncAt: String? = null,
    @SerialName("stale_minutes") val staleMinutes: Int? = null,
    @SerialName("pending_commands") val pendingCommands: Int = 0,
)

@Serializable
data class PhoneCommand(
    val id: String,
    @SerialName("device_id") val deviceId: String = "",
    val kind: String = "",
    val payload: JsonObject = JsonObject(emptyMap()),
    val summary: String = "",
    val status: String = "",
    val result: String = "",
    val error: String = "",
    @SerialName("expires_at") val expiresAt: String? = null,
    @SerialName("approved_at") val approvedAt: String? = null,
    @SerialName("completed_at") val completedAt: String? = null,
    @SerialName("created_at") val createdAt: String = "",
)

@Serializable
data class PhoneCommandResult(
    val status: String,
    val result: String = "",
    val error: String = "",
)

@Serializable
data class PhoneDeviceIds(
    val kind: String = "",
    @SerialName("external_ids") val externalIds: List<String> = emptyList(),
)
