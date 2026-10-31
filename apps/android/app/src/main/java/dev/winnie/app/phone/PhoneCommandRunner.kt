package dev.winnie.app.phone

import android.content.ContentValues
import android.content.Context
import android.provider.CalendarContract
import android.telephony.SmsManager
import dev.winnie.app.data.PhoneCommand
import dev.winnie.app.phone.sources.CalendarSource
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.util.TimeZone
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull

/** The outcome reported back to the server for one command. */
data class CommandOutcome(
    val status: String,
    val result: String = "",
    val error: String = "",
)

/**
 * Executes the writes the owner has already confirmed in chat.
 *
 * This class only touches the device; marking the command dispatched and
 * reporting the result is the engine's job. A failed command reports the
 * exception text, which is what the owner sees in the chat next.
 */
class PhoneCommandRunner(private val context: Context) {

    private val calendar = CalendarSource(context)

    fun run(command: PhoneCommand): CommandOutcome = try {
        when (command.kind) {
            "calendar.create" -> createEvent(command.payload)
            "calendar.update" -> updateEvent(command.payload)
            "calendar.delete" -> deleteEvent(command.payload)
            "sms.send" -> sendSms(command.payload)
            else -> CommandOutcome(
                status = "failed",
                error = "Unsupported command: ${command.kind}",
            )
        }
    } catch (e: SecurityException) {
        CommandOutcome(
            status = "failed",
            error = "Permission denied: ${e.message ?: "the scope is not granted"}",
        )
    } catch (e: Exception) {
        CommandOutcome(
            status = "failed",
            error = e.message ?: e.javaClass.simpleName,
        )
    }

    private fun createEvent(payload: JsonObject): CommandOutcome {
        val title = payload.text("title")
        if (title.isBlank()) return failed("The event has no title.")
        val start = payload.time("starts_at")
            ?: return failed("The event has no valid start time.")
        val allDay = payload.flag("all_day")
        val end = payload.time("ends_at") ?: (start + ONE_HOUR_MS)
        val calendarName = payload.text("calendar_name")
        val target = calendar.writableCalendar(calendarName)
            ?: return failed("No writable calendar is available on this phone.")

        val values = ContentValues().apply {
            put(CalendarContract.Events.CALENDAR_ID, target.first)
            put(CalendarContract.Events.TITLE, title)
            put(
                CalendarContract.Events.DTSTART,
                if (allDay) utcStartOfDay(start) else start,
            )
            val allDayEnd = if (allDay) {
                // DTEND is exclusive for all-day events, and an event must have
                // a positive length: an empty day range becomes one full day.
                maxOf(utcStartOfDay(end), utcStartOfDay(start) + ONE_DAY_MS)
            } else {
                end
            }
            put(CalendarContract.Events.DTEND, allDayEnd)
            put(CalendarContract.Events.ALL_DAY, if (allDay) 1 else 0)
            put(
                CalendarContract.Events.EVENT_TIMEZONE,
                if (allDay) "UTC" else payload.text("timezone").ifBlank {
                    TimeZone.getDefault().id
                },
            )
            payload.text("location").takeIf { it.isNotBlank() }?.let {
                put(CalendarContract.Events.EVENT_LOCATION, it)
            }
            payload.text("description").takeIf { it.isNotBlank() }?.let {
                put(CalendarContract.Events.DESCRIPTION, it)
            }
        }
        val id = calendar.insert(values)
            ?: return failed("The calendar provider refused the event.")
        return CommandOutcome(
            status = "succeeded",
            result = "Created event $id in ${target.second.ifBlank { "the primary calendar" }}.",
        )
    }

    private fun updateEvent(payload: JsonObject): CommandOutcome {
        val eventId = payload.text("event_id").toLongOrNull()
            ?: return failed("The event id is not a calendar event id.")
        val values = ContentValues()
        payload.stringOrNull("title")?.let { values.put(CalendarContract.Events.TITLE, it) }
        payload.stringOrNull("description")?.let {
            values.put(CalendarContract.Events.DESCRIPTION, it)
        }
        payload.stringOrNull("location")?.let {
            values.put(CalendarContract.Events.EVENT_LOCATION, it)
        }
        payload.time("starts_at")?.let { values.put(CalendarContract.Events.DTSTART, it) }
        payload.time("ends_at")?.let { values.put(CalendarContract.Events.DTEND, it) }
        if (values.size() == 0) return failed("The command has nothing to change.")
        val changed = calendar.update(eventId, values)
        return if (changed > 0) {
            CommandOutcome(status = "succeeded", result = "Updated event $eventId.")
        } else {
            failed("Event $eventId was not found.")
        }
    }

    private fun deleteEvent(payload: JsonObject): CommandOutcome {
        val eventId = payload.text("event_id").toLongOrNull()
            ?: return failed("The event id is not a calendar event id.")
        val removed = calendar.delete(eventId)
        return if (removed > 0) {
            CommandOutcome(status = "succeeded", result = "Deleted event $eventId.")
        } else {
            failed("Event $eventId was not found.")
        }
    }

    @Suppress("DEPRECATION")
    private fun sendSms(payload: JsonObject): CommandOutcome {
        val address = payload.text("address")
        val body = payload.text("body")
        if (address.isBlank() || body.isBlank()) {
            return failed("The SMS needs a recipient and a body.")
        }
        // SmsManager became a real system service in API 31; older releases
        // only have the deprecated default instance.
        val manager = context.getSystemService(SmsManager::class.java)
            ?: SmsManager.getDefault()
        val parts = manager.divideMessage(body)
        if (parts.size <= 1) {
            manager.sendTextMessage(address, null, body, null, null)
        } else {
            manager.sendMultipartTextMessage(address, null, parts, null, null)
        }
        return CommandOutcome(
            status = "succeeded",
            result = "Submitted the SMS to $address. Delivery is not confirmed by the app.",
        )
    }

    private fun failed(error: String) = CommandOutcome(status = "failed", error = error)

    private fun utcStartOfDay(millis: Long): Long = Instant.ofEpochMilli(millis)
        .atZone(ZoneId.systemDefault())
        .toLocalDate()
        .atStartOfDay(ZoneOffset.UTC)
        .toInstant()
        .toEpochMilli()

    private fun JsonObject.text(name: String): String =
        (this[name] as? JsonPrimitive)?.contentOrNull.orEmpty()

    private fun JsonObject.stringOrNull(name: String): String? =
        (this[name] as? JsonPrimitive)?.contentOrNull

    private fun JsonObject.flag(name: String): Boolean =
        (this[name] as? JsonPrimitive)?.booleanOrNull ?: false

    private fun JsonObject.time(name: String): Long? {
        val raw = text(name)
        if (raw.isBlank()) return null
        return runCatching { OffsetDateTime.parse(raw).toInstant().toEpochMilli() }
            .getOrNull()
    }

    private companion object {
        const val ONE_HOUR_MS = 60L * 60L * 1000L
        const val ONE_DAY_MS = 24L * 60L * 60L * 1000L
    }
}
