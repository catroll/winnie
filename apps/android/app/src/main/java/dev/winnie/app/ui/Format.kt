package dev.winnie.app.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import dev.winnie.app.AppStrings
import dev.winnie.app.R
import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

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

@Composable
fun statusLabel(status: String): String {
    val id = when (status) {
        "created" -> R.string.status_draft
        "uploaded" -> R.string.status_uploaded
        "queued" -> R.string.status_queued
        "transcribing" -> R.string.status_transcribing
        "transcribed" -> R.string.status_transcribed
        "translating" -> R.string.status_translating
        "summarizing" -> R.string.status_writing_minutes
        "ready" -> R.string.status_ready
        "failed" -> R.string.status_failed
        else -> null
    }
    return if (id != null) stringResource(id) else status.replaceFirstChar { it.uppercase() }
}

/** A title for a new recording, localised with the device's date format. */
fun defaultMeetingTitle(): String {
    val formatted = DateTimeFormatter
        .ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT)
        .withLocale(Locale.getDefault())
        .format(LocalDateTime.now())
    return AppStrings.get(R.string.record_default_title, formatted)
}
