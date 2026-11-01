package dev.winnie.app.phone.sources

import android.content.Context
import android.provider.CallLog
import dev.winnie.app.data.PhoneCallItem
import java.time.Instant

/** Reads the call log page by page, oldest first, keyed by `Calls._ID`. */
class CallLogSource(private val context: Context) {

    fun readPage(afterId: Long, limit: Int): List<PhoneCallItem> {
        val projection = arrayOf(
            CallLog.Calls._ID,
            CallLog.Calls.NUMBER,
            CallLog.Calls.CACHED_NAME,
            CallLog.Calls.TYPE,
            CallLog.Calls.DATE,
            CallLog.Calls.DURATION,
            CallLog.Calls.NEW,
            CallLog.Calls.PHONE_ACCOUNT_ID,
        )
        val cursor = try {
            context.contentResolver.query(
                CallLog.Calls.CONTENT_URI,
                projection,
                "${CallLog.Calls._ID} > ?",
                arrayOf(afterId.toString()),
                "${CallLog.Calls._ID} ASC",
            )
        } catch (_: IllegalArgumentException) {
            // Some OEM providers reject PHONE_ACCOUNT_ID; fall back without it.
            readWithoutAccount(afterId, limit)
        } ?: return emptyList()

        return cursor.use { mapCallCursor(it, limit) }
    }

    /**
     * Whether a row newer than the sync cursor exists. This is the 5-second
     * loop's cheap check: a primary-key range with a single-row limit, not a
     * page read. A provider that refuses the query reports "no change" and lets
     * the upload step record the real error.
     */
    fun hasNew(afterId: Long): Boolean {
        val cursor = try {
            context.contentResolver.query(
                CallLog.Calls.CONTENT_URI,
                arrayOf(CallLog.Calls._ID),
                "${CallLog.Calls._ID} > ?",
                arrayOf(afterId.toString()),
                "${CallLog.Calls._ID} ASC",
            )
        } catch (_: Exception) {
            null
        } ?: return false
        return cursor.use { it.moveToFirst() }
    }

    private fun readWithoutAccount(afterId: Long, limit: Int) =
        context.contentResolver.query(
            CallLog.Calls.CONTENT_URI,
            arrayOf(
                CallLog.Calls._ID,
                CallLog.Calls.NUMBER,
                CallLog.Calls.CACHED_NAME,
                CallLog.Calls.TYPE,
                CallLog.Calls.DATE,
                CallLog.Calls.DURATION,
                CallLog.Calls.NEW,
            ),
            "${CallLog.Calls._ID} > ?",
            arrayOf(afterId.toString()),
            "${CallLog.Calls._ID} ASC",
        )

}

internal fun mapCallCursor(cursor: android.database.Cursor, limit: Int): List<PhoneCallItem> {
    val id = cursor.getColumnIndexOrThrow(CallLog.Calls._ID)
    val number = cursor.getColumnIndex(CallLog.Calls.NUMBER)
    val name = cursor.getColumnIndex(CallLog.Calls.CACHED_NAME)
    val type = cursor.getColumnIndex(CallLog.Calls.TYPE)
    val date = cursor.getColumnIndex(CallLog.Calls.DATE)
    val duration = cursor.getColumnIndex(CallLog.Calls.DURATION)
    val new = cursor.getColumnIndex(CallLog.Calls.NEW)
    val account = cursor.getColumnIndex(CallLog.Calls.PHONE_ACCOUNT_ID)

    val items = ArrayList<PhoneCallItem>(minOf(limit, cursor.count))
    while (cursor.moveToNext() && items.size < limit) {
        val timestamp = if (date >= 0) cursor.getLong(date) else 0L
        items += PhoneCallItem(
            externalId = cursor.getLong(id).toString(),
            number = if (number >= 0) cursor.getString(number).orEmpty() else "",
            cachedName = if (name >= 0) cursor.getString(name).orEmpty() else "",
            direction = when (if (type >= 0) cursor.getInt(type) else 0) {
                CallLog.Calls.INCOMING_TYPE -> "incoming"
                CallLog.Calls.OUTGOING_TYPE -> "outgoing"
                CallLog.Calls.MISSED_TYPE -> "missed"
                CallLog.Calls.REJECTED_TYPE -> "rejected"
                CallLog.Calls.BLOCKED_TYPE -> "blocked"
                CallLog.Calls.VOICEMAIL_TYPE -> "voicemail"
                else -> "unknown"
            },
            startedAt = timestamp.takeIf { value -> value > 0L }
                ?.let { value -> Instant.ofEpochMilli(value).toString() },
            durationSeconds = if (duration >= 0) cursor.getInt(duration) else 0,
            simAccount = if (account >= 0) cursor.getString(account).orEmpty() else "",
            isNew = if (new >= 0) cursor.getInt(new) != 0 else false,
        )
    }
    return items
}
