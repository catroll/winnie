package dev.winnie.app.phone.sources

import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.provider.CalendarContract
import dev.winnie.app.data.PhoneCalendarItem
import java.time.Instant

/**
 * Calendar reads and writes through `CalendarContract`.
 *
 * The sync reads `Events` rows, not expanded `Instances`: a recurring event
 * appears once with its RRULE instead of once per occurrence. That keeps the
 * event id used for update/delete identical to the id the assistant sees, which
 * matters more here than showing every occurrence.
 */
class CalendarSource(private val context: Context) {

    fun readWindow(fromMillis: Long, toMillis: Long): List<PhoneCalendarItem> {
        val names = calendarNames()
        val projection = arrayOf(
            CalendarContract.Events._ID,
            CalendarContract.Events.CALENDAR_ID,
            CalendarContract.Events.TITLE,
            CalendarContract.Events.DESCRIPTION,
            CalendarContract.Events.EVENT_LOCATION,
            CalendarContract.Events.DTSTART,
            CalendarContract.Events.DTEND,
            CalendarContract.Events.ALL_DAY,
            CalendarContract.Events.EVENT_TIMEZONE,
            CalendarContract.Events.RRULE,
            CalendarContract.Events.ORGANIZER,
            CalendarContract.Events.STATUS,
        )
        val cursor = try {
            context.contentResolver.query(
                CalendarContract.Events.CONTENT_URI,
                projection,
                "${CalendarContract.Events.DELETED} = 0 AND " +
                    "${CalendarContract.Events.DTSTART} >= ? AND " +
                    "${CalendarContract.Events.DTSTART} <= ?",
                arrayOf(fromMillis.toString(), toMillis.toString()),
                "${CalendarContract.Events.DTSTART} ASC",
            )
        } catch (_: Exception) {
            return emptyList()
        } ?: return emptyList()

        return cursor.use { mapCalendarCursor(it, names) }
    }

    fun calendarNames(): Map<Long, String> {
        val names = mutableMapOf<Long, String>()
        val projection = arrayOf(
            CalendarContract.Calendars._ID,
            CalendarContract.Calendars.CALENDAR_DISPLAY_NAME,
        )
        val cursor = try {
            context.contentResolver.query(
                CalendarContract.Calendars.CONTENT_URI,
                projection,
                null,
                null,
                null,
            )
        } catch (_: Exception) {
            return names
        } ?: return names
        cursor.use {
            val id = it.getColumnIndexOrThrow(CalendarContract.Calendars._ID)
            val display = it.getColumnIndex(
                CalendarContract.Calendars.CALENDAR_DISPLAY_NAME
            )
            while (it.moveToNext()) {
                names[it.getLong(id)] =
                    if (display >= 0) it.getString(display).orEmpty() else ""
            }
        }
        return names
    }

    /**
     * The calendar a new event goes into: the preferred display name when it
     * exists and is writable, otherwise the primary writable calendar.
     */
    fun writableCalendar(preferredName: String = ""): Pair<Long, String>? {
        val projection = arrayOf(
            CalendarContract.Calendars._ID,
            CalendarContract.Calendars.CALENDAR_DISPLAY_NAME,
            CalendarContract.Calendars.IS_PRIMARY,
        )
        val cursor = try {
            context.contentResolver.query(
                CalendarContract.Calendars.CONTENT_URI,
                projection,
                "${CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL} >= ?",
                arrayOf(CalendarContract.Calendars.CAL_ACCESS_CONTRIBUTOR.toString()),
                "${CalendarContract.Calendars.IS_PRIMARY} DESC, " +
                    "${CalendarContract.Calendars.CALENDAR_DISPLAY_NAME} ASC",
            )
        } catch (_: Exception) {
            return null
        } ?: return null

        val candidates = mutableListOf<Pair<Long, String>>()
        cursor.use {
            val id = it.getColumnIndexOrThrow(CalendarContract.Calendars._ID)
            val display = it.getColumnIndex(
                CalendarContract.Calendars.CALENDAR_DISPLAY_NAME
            )
            while (it.moveToNext()) {
                candidates += it.getLong(id) to
                    (if (display >= 0) it.getString(display).orEmpty() else "")
            }
        }
        if (preferredName.isNotBlank()) {
            candidates.firstOrNull {
                it.second.equals(preferredName, ignoreCase = true)
            }?.let { return it }
            candidates.firstOrNull {
                it.second.contains(preferredName, ignoreCase = true)
            }?.let { return it }
        }
        return candidates.firstOrNull()
    }

    fun insert(values: ContentValues): Long? {
        val uri = context.contentResolver.insert(
            CalendarContract.Events.CONTENT_URI,
            values,
        ) ?: return null
        return ContentUris.parseId(uri)
    }

    fun update(eventId: Long, values: ContentValues): Int =
        context.contentResolver.update(
            ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, eventId),
            values,
            null,
            null,
        )

    fun delete(eventId: Long): Int =
        context.contentResolver.delete(
            ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, eventId),
            null,
            null,
        )

}

internal fun mapCalendarCursor(
    cursor: android.database.Cursor,
    names: Map<Long, String>,
): List<PhoneCalendarItem> {
    val id = cursor.getColumnIndexOrThrow(CalendarContract.Events._ID)
    val calendarId = cursor.getColumnIndex(CalendarContract.Events.CALENDAR_ID)
    val title = cursor.getColumnIndex(CalendarContract.Events.TITLE)
    val description = cursor.getColumnIndex(CalendarContract.Events.DESCRIPTION)
    val location = cursor.getColumnIndex(CalendarContract.Events.EVENT_LOCATION)
    val start = cursor.getColumnIndex(CalendarContract.Events.DTSTART)
    val end = cursor.getColumnIndex(CalendarContract.Events.DTEND)
    val allDay = cursor.getColumnIndex(CalendarContract.Events.ALL_DAY)
    val timezone = cursor.getColumnIndex(CalendarContract.Events.EVENT_TIMEZONE)
    val rrule = cursor.getColumnIndex(CalendarContract.Events.RRULE)
    val organizer = cursor.getColumnIndex(CalendarContract.Events.ORGANIZER)
    val status = cursor.getColumnIndex(CalendarContract.Events.STATUS)

    val items = ArrayList<PhoneCalendarItem>(cursor.count)
    while (cursor.moveToNext()) {
        val calendar = if (calendarId >= 0) cursor.getLong(calendarId) else 0L
        items += PhoneCalendarItem(
            externalId = cursor.getLong(id).toString(),
            calendarId = calendar.toString(),
            calendarName = names[calendar].orEmpty(),
            title = if (title >= 0) cursor.getString(title).orEmpty() else "",
            description = if (description >= 0) cursor.getString(description).orEmpty() else "",
            location = if (location >= 0) cursor.getString(location).orEmpty() else "",
            startsAt = if (start >= 0) {
                cursor.getLong(start).takeIf { value -> value > 0L }
                    ?.let { value -> Instant.ofEpochMilli(value).toString() }
            } else {
                null
            },
            endsAt = if (end >= 0) {
                cursor.getLong(end).takeIf { value -> value > 0L }
                    ?.let { value -> Instant.ofEpochMilli(value).toString() }
            } else {
                null
            },
            allDay = allDay >= 0 && cursor.getInt(allDay) != 0,
            timezone = if (timezone >= 0) cursor.getString(timezone).orEmpty() else "",
            recurrence = if (rrule >= 0) cursor.getString(rrule).orEmpty() else "",
            organizer = if (organizer >= 0) cursor.getString(organizer).orEmpty() else "",
            status = when (if (status >= 0) cursor.getInt(status) else 1) {
                CalendarContract.Events.STATUS_TENTATIVE -> "tentative"
                CalendarContract.Events.STATUS_CANCELED -> "cancelled"
                else -> "confirmed"
            },
        )
    }
    return items
}
