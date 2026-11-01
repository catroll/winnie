package dev.winnie.app.phone

import android.Manifest
import android.content.Context
import android.net.Uri
import android.os.Process
import android.provider.CalendarContract
import android.provider.CallLog
import android.provider.ContactsContract
import android.provider.Telephony
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * What the content-provider scopes actually answered, as this process saw it.
 *
 * A provider that will not hand over its rows has three ways to say so: throw
 * `SecurityException`, return a null cursor, or return an empty cursor. Only the
 * first reads as an error, so the other two make a phone with a full call log
 * look like a phone with none — which is exactly what some vendor ROMs do on
 * purpose ("blank data" / 虚拟权限: keep the grant, answer with nothing).
 *
 * This probe records the permission state and the raw provider answer for every
 * content-provider scope. The device snapshot carries it to the server, so "this
 * phone has no contacts" and "this phone is not allowed to see its contacts"
 * stop being the same sentence.
 */
class ScopeReadHealth(private val context: Context) {

    fun probe(): JsonObject = buildJsonObject {
        put("uid", Process.myUid())
        // /data/user/<user>/… names the profile this process runs in; a phone
        // whose data lives in another profile looks exactly like an empty one.
        put("data_dir", context.filesDir.absolutePath)
        put("package", context.packageName)
        put(
            "message",
            probeUri(Telephony.Sms.CONTENT_URI, Manifest.permission.READ_SMS),
        )
        put(
            "call",
            probeUri(CallLog.Calls.CONTENT_URI, Manifest.permission.READ_CALL_LOG),
        )
        put(
            "contact",
            probeUri(
                ContactsContract.Contacts.CONTENT_URI,
                Manifest.permission.READ_CONTACTS,
            ),
        )
        put(
            "calendar",
            probeUri(
                CalendarContract.Events.CONTENT_URI,
                Manifest.permission.READ_CALENDAR,
            ),
        )
        put(
            "calendars",
            probeUri(
                CalendarContract.Calendars.CONTENT_URI,
                Manifest.permission.READ_CALENDAR,
            ),
        )
    }

    private fun probeUri(uri: Uri, permission: String): JsonObject =
        recordRead(PhonePermissions.granted(context, permission)) {
            val cursor = context.contentResolver.query(
                uri,
                arrayOf("_id"),
                null,
                null,
                null,
            ) ?: return@recordRead null
            cursor.use { it.count }
        }
}

/**
 * The JSON for one provider answer.
 *
 * Kept free of Android so the three distinguishable outcomes can be pinned by a
 * test: a row count, a null cursor (the ROM answered "nothing" instead of
 * refusing), and an exception.
 */
internal fun recordRead(granted: Boolean, read: () -> Int?): JsonObject = buildJsonObject {
    put("permission", if (granted) "granted" else "denied")
    val rows = try {
        read()
    } catch (e: SecurityException) {
        put("error", "security: ${e.message.orEmpty().take(160)}")
        return@buildJsonObject
    } catch (e: Exception) {
        put("error", "${e.javaClass.simpleName}: ${e.message.orEmpty().take(160)}")
        return@buildJsonObject
    }
    if (rows == null) put("error", "null_cursor") else put("rows", rows)
}
