package dev.winnie.app.phone

import android.Manifest

/**
 * The pieces of phone data the app can sync.
 *
 * `key` is the server's kind, so the enum is the single place where a UI label
 * and a wire kind are tied together. Nothing is enabled by default: the owner
 * opts in per scope, and a scope only runs while its permissions are granted.
 */
enum class PhoneScope(
    val key: String,
    val title: String,
    val description: String,
    val permissions: List<String> = emptyList(),
    val needsNotificationAccess: Boolean = false,
    val needsBackgroundLocation: Boolean = false,
) {
    CALENDAR(
        key = "calendar",
        title = "Calendar",
        description = "Read events; the assistant can add, change or delete one after you confirm.",
        permissions = listOf(
            Manifest.permission.READ_CALENDAR,
            Manifest.permission.WRITE_CALENDAR,
        ),
    ),
    CALLS(
        key = "call",
        title = "Call log",
        description = "Incoming, outgoing, missed and rejected calls with duration.",
        permissions = listOf(Manifest.permission.READ_CALL_LOG),
    ),
    MESSAGES(
        key = "message",
        title = "SMS",
        description = "Text messages. The assistant can send one only after you confirm it in chat.",
        permissions = listOf(
            Manifest.permission.READ_SMS,
            Manifest.permission.SEND_SMS,
        ),
    ),
    CONTACTS(
        key = "contact",
        title = "Contacts",
        description = "Names, phone numbers, email addresses and organisation.",
        permissions = listOf(Manifest.permission.READ_CONTACTS),
    ),
    NOTIFICATIONS(
        key = "notification",
        title = "Notifications",
        description = "What apps notified you about, including verification codes.",
        needsNotificationAccess = true,
    ),
    LOCATION(
        key = "location",
        title = "Location",
        description = "One position per sync. Background access is needed for a trail.",
        needsBackgroundLocation = true,
    ),
    APPS(
        key = "app",
        title = "Installed apps",
        description = "Package names, versions, install dates and system flags.",
    ),
    DEVICE(
        key = "device",
        title = "Device info",
        description = "Model, Android version, battery, storage, network, SIM and accounts.",
    ),
    ;

    /** Runtime permissions to request, including location's foreground pair. */
    val runtimePermissions: List<String>
        get() = when (this) {
            LOCATION -> listOf(
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION,
            )
            else -> permissions
        }

    companion object {
        fun fromKey(key: String): PhoneScope? = entries.firstOrNull { it.key == key }
    }
}
