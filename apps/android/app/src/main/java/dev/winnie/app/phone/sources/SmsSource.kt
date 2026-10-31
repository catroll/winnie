package dev.winnie.app.phone.sources

import android.content.Context
import android.provider.Telephony
import dev.winnie.app.data.PhoneMessageItem
import java.time.Instant

/**
 * Reads text messages (the `sms` provider, not MMS parts) page by page.
 *
 * `Telephony.Sms._ID` is monotonic, so the sync cursor is the highest id read.
 * The server treats it as opaque. Edits and deletions made on the phone are not
 * visible through this cursor; a deletion is reported only when the owner
 * removes a message through the app, which this MVP does not do.
 */
class SmsSource(private val context: Context) {

    fun readPage(afterId: Long, limit: Int): List<PhoneMessageItem> {
        val projection = arrayOf(
            Telephony.Sms._ID,
            Telephony.Sms.THREAD_ID,
            Telephony.Sms.ADDRESS,
            Telephony.Sms.DATE,
            Telephony.Sms.BODY,
            Telephony.Sms.TYPE,
            Telephony.Sms.READ,
            Telephony.Sms.SUBJECT,
        )
        val cursor = context.contentResolver.query(
            Telephony.Sms.CONTENT_URI,
            projection,
            "${Telephony.Sms._ID} > ?",
            arrayOf(afterId.toString()),
            "${Telephony.Sms._ID} ASC",
        ) ?: return emptyList()

        return cursor.use { mapSmsCursor(it, limit) }
    }

}

internal fun mapSmsCursor(cursor: android.database.Cursor, limit: Int): List<PhoneMessageItem> {
    val id = cursor.getColumnIndexOrThrow(Telephony.Sms._ID)
    val thread = cursor.getColumnIndex(Telephony.Sms.THREAD_ID)
    val address = cursor.getColumnIndex(Telephony.Sms.ADDRESS)
    val date = cursor.getColumnIndex(Telephony.Sms.DATE)
    val body = cursor.getColumnIndex(Telephony.Sms.BODY)
    val type = cursor.getColumnIndex(Telephony.Sms.TYPE)
    val read = cursor.getColumnIndex(Telephony.Sms.READ)
    val subject = cursor.getColumnIndex(Telephony.Sms.SUBJECT)

    val items = ArrayList<PhoneMessageItem>(minOf(limit, cursor.count))
    while (cursor.moveToNext() && items.size < limit) {
        val timestamp = if (date >= 0) cursor.getLong(date) else 0L
        items += PhoneMessageItem(
            externalId = cursor.getLong(id).toString(),
            threadId = if (thread >= 0) cursor.getLong(thread).toString() else "",
            address = if (address >= 0) cursor.getString(address).orEmpty() else "",
            direction = when (if (type >= 0) cursor.getInt(type) else 0) {
                Telephony.Sms.MESSAGE_TYPE_SENT -> "sent"
                Telephony.Sms.MESSAGE_TYPE_DRAFT -> "draft"
                Telephony.Sms.MESSAGE_TYPE_OUTBOX -> "outbox"
                Telephony.Sms.MESSAGE_TYPE_FAILED -> "failed"
                Telephony.Sms.MESSAGE_TYPE_QUEUED -> "queued"
                else -> "inbox"
            },
            body = if (body >= 0) cursor.getString(body).orEmpty() else "",
            subject = if (subject >= 0) cursor.getString(subject).orEmpty() else "",
            receivedAt = timestamp.takeIf { value -> value > 0L }
                ?.let { value -> Instant.ofEpochMilli(value).toString() },
            isRead = if (read >= 0) cursor.getInt(read) != 0 else true,
        )
    }
    return items
}
