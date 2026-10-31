package dev.winnie.app.phone.sources

import android.database.MatrixCursor
import android.provider.CalendarContract
import android.provider.CallLog
import android.provider.Telephony
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * The provider rows are the only place where column names and types can drift
 * from the server model, so the mappers are tested directly against real
 * cursors instead of through a database.
 */
@RunWith(RobolectricTestRunner::class)
class PhoneSourceMappingTest {

    @Test
    fun mapsSmsRowsAndHonoursTheLimit() {
        val cursor = MatrixCursor(
            arrayOf(
                Telephony.Sms._ID,
                Telephony.Sms.THREAD_ID,
                Telephony.Sms.ADDRESS,
                Telephony.Sms.DATE,
                Telephony.Sms.BODY,
                Telephony.Sms.TYPE,
                Telephony.Sms.READ,
                Telephony.Sms.SUBJECT,
            )
        )
        cursor.addRow(
            arrayOf<Any?>(
                7L,
                3L,
                "+8613800138000",
                1_700_000_000_000L,
                "hello",
                Telephony.Sms.MESSAGE_TYPE_INBOX,
                0,
                null,
            )
        )
        cursor.addRow(
            arrayOf<Any?>(
                8L,
                3L,
                "+8613800138000",
                1_700_000_060_000L,
                "sent back",
                Telephony.Sms.MESSAGE_TYPE_SENT,
                1,
                "subject",
            )
        )
        cursor.moveToPosition(-1)

        val items = mapSmsCursor(cursor, 10)

        assertEquals(2, items.size)
        assertEquals("7", items[0].externalId)
        assertEquals("inbox", items[0].direction)
        assertFalse(items[0].isRead)
        assertEquals("2023-11-14T22:13:20Z", items[0].receivedAt)
        assertEquals("sent", items[1].direction)
        assertTrue(items[1].isRead)
        assertEquals("subject", items[1].subject)

        cursor.moveToPosition(-1)
        assertEquals(1, mapSmsCursor(cursor, 1).size)
    }

    @Test
    fun mapsCallTypesDurationsAndNewFlag() {
        val cursor = MatrixCursor(
            arrayOf(
                CallLog.Calls._ID,
                CallLog.Calls.NUMBER,
                CallLog.Calls.CACHED_NAME,
                CallLog.Calls.TYPE,
                CallLog.Calls.DATE,
                CallLog.Calls.DURATION,
                CallLog.Calls.NEW,
                CallLog.Calls.PHONE_ACCOUNT_ID,
            )
        )
        cursor.addRow(
            arrayOf<Any?>(
                4L,
                "+8613900139000",
                "Zhang",
                CallLog.Calls.MISSED_TYPE,
                1_700_000_000_000L,
                0,
                1,
                "sim-1",
            )
        )
        cursor.addRow(
            arrayOf<Any?>(
                5L,
                null,
                null,
                CallLog.Calls.OUTGOING_TYPE,
                1_700_000_100_000L,
                95,
                0,
                "sim-1",
            )
        )
        cursor.moveToPosition(-1)

        val items = mapCallCursor(cursor, 10)

        assertEquals("missed", items[0].direction)
        assertTrue(items[0].isNew)
        assertEquals("Zhang", items[0].cachedName)
        assertEquals("sim-1", items[0].simAccount)
        assertEquals("outgoing", items[1].direction)
        assertEquals(95, items[1].durationSeconds)
        assertEquals("", items[1].number)
    }

    @Test
    fun mapsCalendarRecurrenceAndAllDay() {
        val cursor = MatrixCursor(
            arrayOf(
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
        )
        cursor.addRow(
            arrayOf<Any?>(
                11L,
                2L,
                "Standup",
                "daily sync",
                "Room 4",
                1_700_000_000_000L,
                1_700_000_900_000L,
                0,
                "Asia/Shanghai",
                "FREQ=DAILY",
                "boss@example.com",
                CalendarContract.Events.STATUS_TENTATIVE,
            )
        )
        cursor.addRow(
            arrayOf<Any?>(
                12L,
                2L,
                "Holiday",
                null,
                null,
                null,
                null,
                1,
                "UTC",
                null,
                null,
                CalendarContract.Events.STATUS_CONFIRMED,
            )
        )
        cursor.moveToPosition(-1)

        val items = mapCalendarCursor(cursor, mapOf(2L to "Work"))

        assertEquals(2, items.size)
        assertEquals("Work", items[0].calendarName)
        assertEquals("FREQ=DAILY", items[0].recurrence)
        assertEquals("tentative", items[0].status)
        assertEquals("2023-11-14T22:13:20Z", items[0].startsAt)
        assertFalse(items[0].allDay)
        assertTrue(items[1].allDay)
        assertNull(items[1].startsAt)
        assertEquals("", items[1].description)
        assertEquals("Work", items[1].calendarName)
    }

    /**
     * A Huawei/HarmonyOS calendar provider stores `TimeZone.toString()` in
     * `EVENT_TIMEZONE`. Sending the 267-character dump made the server reject
     * the whole calendar batch, so the mapper has to hand over the id.
     */
    @Test
    fun reducesAProvidersJavaTimezoneDumpToItsId() {
        val cursor = MatrixCursor(
            arrayOf(
                CalendarContract.Events._ID,
                CalendarContract.Events.EVENT_TIMEZONE,
            )
        )
        cursor.addRow(arrayOf<Any?>(21L, JAVA_TIMEZONE_DUMP))
        cursor.moveToPosition(-1)

        val items = mapCalendarCursor(cursor, emptyMap())

        assertEquals("Asia/Shanghai", items[0].timezone)
        assertTrue(items[0].timezone.length <= MAX_TIMEZONE_CHARS)
        assertEquals("Asia/Shanghai", normalizeTimeZone(JAVA_TIMEZONE_DUMP))
        assertEquals("Asia/Shanghai", normalizeTimeZone("  Asia/Shanghai  "))
        assertEquals("", normalizeTimeZone("   "))
        assertEquals(
            "x".repeat(MAX_TIMEZONE_CHARS),
            normalizeTimeZone("x".repeat(200)),
        )
        assertEquals("UTC", normalizeTimeZone("UTC"))
    }

    private companion object {
        const val JAVA_TIMEZONE_DUMP =
            "java.util.SimpleTimeZone[id=Asia/Shanghai,offset=28800000," +
                "dstSavings=0,useDaylight=false,transitions=0,lastRule=null," +
                "startMode=0,startMonth=0,startDay=0,startDayOfWeek=0," +
                "startTime=0,startTimeMode=0,endMode=0,endMonth=0,endDay=0," +
                "endDayOfWeek=0,endTime=0,endTimeMode=0]"
    }
}
