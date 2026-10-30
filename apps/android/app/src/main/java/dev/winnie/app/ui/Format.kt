package dev.winnie.app.ui

import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

/**
 * The backend serialises naive UTC timestamps (SQLite) as ISO strings without
 * an offset. Parse those as UTC; accept zoned values too so the UI keeps
 * working if the deployment moves to PostgreSQL.
 */
fun parseApiTime(raw: String): Instant? =
    runCatching { Instant.parse(raw) }.getOrNull()
        ?: runCatching { OffsetDateTime.parse(raw).toInstant() }.getOrNull()
        ?: runCatching { LocalDateTime.parse(raw).toInstant(ZoneOffset.UTC) }.getOrNull()

fun formatMeetingTime(raw: String): String {
    val instant = parseApiTime(raw) ?: return raw
    return DateTimeFormatter
        .ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT)
        .withZone(ZoneId.systemDefault())
        .format(instant)
}

fun formatTimestamp(raw: String): String = formatMeetingTime(raw)

/** Formats a segment offset as `m:ss` or `h:mm:ss`. */
fun formatOffset(milliseconds: Int?): String {
    if (milliseconds == null || milliseconds <= 0) return "0:00"
    val totalSeconds = milliseconds / 1000
    val hours = totalSeconds / 3600
    val minutes = (totalSeconds % 3600) / 60
    val seconds = totalSeconds % 60
    return if (hours > 0) {
        "%d:%02d:%02d".format(hours, minutes, seconds)
    } else {
        "%d:%02d".format(minutes, seconds)
    }
}

fun formatElapsed(milliseconds: Long): String {
    val totalSeconds = milliseconds / 1000
    val minutes = totalSeconds / 60
    val seconds = totalSeconds % 60
    return "%02d:%02d".format(minutes, seconds)
}

fun statusLabel(status: String): String = when (status) {
    "created" -> "Draft"
    "uploaded" -> "Uploaded"
    "queued" -> "Queued"
    "transcribing" -> "Transcribing"
    "transcribed" -> "Transcribed"
    "translating" -> "Translating"
    "summarizing" -> "Writing minutes"
    "ready" -> "Ready"
    "failed" -> "Failed"
    else -> status.replaceFirstChar { it.uppercase() }
}

fun defaultMeetingTitle(): String =
    "Meeting " + LocalDateTime.now().format(DateTimeFormatter.ofPattern("MMM d, HH:mm"))
