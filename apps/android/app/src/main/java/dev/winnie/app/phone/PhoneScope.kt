package dev.winnie.app.phone

import android.Manifest
import androidx.annotation.StringRes
import dev.winnie.app.R

/**
 * The pieces of phone data the app can sync.
 *
 * `key` is the server's kind, so the enum is the single place where a UI label
 * and a wire kind are tied together. Nothing is enabled by default: the owner
 * opts in per scope, and a scope only runs while its permissions are granted.
 */
enum class PhoneScope(
    val key: String,
    @StringRes val titleRes: Int,
    @StringRes val descriptionRes: Int,
    val permissions: List<String> = emptyList(),
    val needsNotificationAccess: Boolean = false,
    val needsBackgroundLocation: Boolean = false,
) {
    CALENDAR(
        key = "calendar",
        titleRes = R.string.scope_calendar_title,
        descriptionRes = R.string.scope_calendar_description,
        permissions = listOf(
            Manifest.permission.READ_CALENDAR,
            Manifest.permission.WRITE_CALENDAR,
        ),
    ),
    CALLS(
        key = "call",
        titleRes = R.string.scope_call_title,
        descriptionRes = R.string.scope_call_description,
        permissions = listOf(Manifest.permission.READ_CALL_LOG),
    ),
    MESSAGES(
        key = "message",
        titleRes = R.string.scope_message_title,
        descriptionRes = R.string.scope_message_description,
        permissions = listOf(
            Manifest.permission.READ_SMS,
            Manifest.permission.SEND_SMS,
        ),
    ),
    CONTACTS(
        key = "contact",
        titleRes = R.string.scope_contact_title,
        descriptionRes = R.string.scope_contact_description,
        permissions = listOf(Manifest.permission.READ_CONTACTS),
    ),
    NOTIFICATIONS(
        key = "notification",
        titleRes = R.string.scope_notification_title,
        descriptionRes = R.string.scope_notification_description,
        needsNotificationAccess = true,
    ),
    LOCATION(
        key = "location",
        titleRes = R.string.scope_location_title,
        descriptionRes = R.string.scope_location_description,
        needsBackgroundLocation = true,
    ),
    APPS(
        key = "app",
        titleRes = R.string.scope_app_title,
        descriptionRes = R.string.scope_app_description,
    ),
    DEVICE(
        key = "device",
        titleRes = R.string.scope_device_title,
        descriptionRes = R.string.scope_device_description,
        // The phone number needs READ_PHONE_NUMBERS on Android 13+ and
        // READ_PHONE_STATE before that; both are requested so one prompt covers
        // either platform.
        permissions = listOf(
            Manifest.permission.READ_PHONE_STATE,
            Manifest.permission.READ_PHONE_NUMBERS,
        ),
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
