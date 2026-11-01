package dev.winnie.app.phone

import java.security.MessageDigest

/**
 * The cadence of the 5-second sync loop.
 *
 * The loop itself ticks every [TICK_MS] while the app process is alive — which
 * includes while the alert foreground service keeps it alive. A tick is only a
 * *check*: each scope has a cadence for how often its provider is read, because
 * reading the whole app list every five seconds would cost far more than the
 * sync it might trigger. A content observer bypasses the cadence through
 * [PhoneChangeFlags].
 */
object PhoneCadence {
    /** The loop period, and the latency for a cheap scope whose observer fired. */
    const val TICK_MS = 5_000L

    /**
     * How often the phone asks for confirmed writes even when nothing local
     * changed. The push stream delivers a `sync` frame immediately, so this is
     * the fallback for an owner who has alerts switched off.
     */
    const val COMMAND_POLL_MS = 30_000L

    const val CALENDAR_MS = 30_000L
    const val CONTACTS_MS = 60_000L
    const val LOCATION_MS = 5 * 60_000L
    const val DEVICE_MS = 5 * 60_000L
    const val APPS_MS = 30 * 60_000L

    fun intervalMs(scope: PhoneScope): Long = when (scope) {
        PhoneScope.CALLS, PhoneScope.MESSAGES, PhoneScope.NOTIFICATIONS -> TICK_MS
        PhoneScope.CALENDAR -> CALENDAR_MS
        PhoneScope.CONTACTS -> CONTACTS_MS
        PhoneScope.LOCATION -> LOCATION_MS
        PhoneScope.DEVICE -> DEVICE_MS
        PhoneScope.APPS -> APPS_MS
    }
}

/** The calendar window every run uploads, shared by the check and the upload. */
object PhoneSyncWindow {
    private const val BACK_DAYS = 180L
    private const val AHEAD_DAYS = 365L
    private const val DAY_MS = 24L * 60L * 60L * 1000L

    fun from(now: Long): Long = now - BACK_DAYS * DAY_MS

    fun to(now: Long): Long = now + AHEAD_DAYS * DAY_MS
}

/** A short, stable digest for a snapshot's contents. */
internal fun snapshotDigest(value: String): String {
    val digest = MessageDigest.getInstance("SHA-256").digest(value.toByteArray())
    return digest.joinToString("") { "%02x".format(it) }.take(32)
}
