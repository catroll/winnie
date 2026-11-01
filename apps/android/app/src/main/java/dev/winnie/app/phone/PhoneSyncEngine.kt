package dev.winnie.app.phone

import android.content.Context
import dev.winnie.app.AppStrings
import dev.winnie.app.R
import dev.winnie.app.data.ApiException
import dev.winnie.app.data.PhoneAppItem
import dev.winnie.app.data.PhoneCalendarItem
import dev.winnie.app.data.PhoneCallItem
import dev.winnie.app.data.PhoneCommandResult
import dev.winnie.app.data.PhoneContactItem
import dev.winnie.app.data.PhoneDevice
import dev.winnie.app.data.PhoneLocationItem
import dev.winnie.app.data.PhoneMessageItem
import dev.winnie.app.data.PhoneNotificationItem
import dev.winnie.app.data.PhoneSyncRequest
import dev.winnie.app.data.UnauthorizedException
import dev.winnie.app.data.WinnieApi
import dev.winnie.app.data.friendlyMessage
import dev.winnie.app.phone.sources.AppSource
import dev.winnie.app.phone.sources.CalendarSource
import dev.winnie.app.phone.sources.CallLogSource
import dev.winnie.app.phone.sources.ContactsSource
import dev.winnie.app.phone.sources.DeviceSource
import dev.winnie.app.phone.sources.LocationSource
import dev.winnie.app.phone.sources.NotificationBuffer
import dev.winnie.app.phone.sources.SmsSource
import java.io.IOException
import java.time.Instant
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.encodeToJsonElement

/** How a sync run ended, for the worker's retry decision and the UI. */
data class PhoneSyncOutcome(
    val skipped: Boolean = false,
    val ok: Boolean = false,
    val unauthorized: Boolean = false,
    val retry: Boolean = false,
    val accepted: Int = 0,
    val commands: Int = 0,
    val errors: List<String> = emptyList(),
    /** Scopes whose step completed; the change detector commits only these. */
    val uploadedScopes: Set<PhoneScope> = emptySet(),
    /** Scopes whose step failed; their fingerprint stays uncommitted. */
    val failedScopes: Set<PhoneScope> = emptySet(),
)

/**
 * One sync run: register the device if needed, upload the scopes the run was
 * asked for, then (optionally) execute confirmed commands.
 *
 * There are two entry points on purpose. [syncNow] is the full run used by the
 * periodic worker, the phone-data screen's button and every scope toggle: it
 * reads every enabled scope. [syncScopes] is the 5-second loop's path and reads
 * only the scopes whose local change check found something, so a tick that
 * detects a new SMS does not re-upload the calendar or the app list.
 *
 * Scopes are isolated: a provider that throws (a vendor ROM refusing a column,
 * a revoked permission) records an error and the other scopes still upload.
 * Only an expired session, a revoked device or a transport failure stops the
 * run.
 */
class PhoneSyncEngine(
    private val context: Context,
    private val prefs: PhonePrefs,
    private val apiProvider: () -> WinnieApi?,
) {

    private val deviceSource = DeviceSource(context)
    private val calendarSource = CalendarSource(context)
    private val callSource = CallLogSource(context)
    private val smsSource = SmsSource(context)
    private val contactsSource = ContactsSource(context)
    private val appSource = AppSource(context)
    private val locationSource = LocationSource(context)
    private val notificationBuffer = NotificationBuffer(context)
    private val commandRunner = PhoneCommandRunner(context)

    /** A full run: every enabled scope, the device snapshot, and commands. */
    suspend fun syncNow(): PhoneSyncOutcome = run(only = null, pollCommands = true)

    /** The 5-second path: only [only], with commands when [pollCommands]. */
    suspend fun syncScopes(
        only: Set<PhoneScope>,
        pollCommands: Boolean = false,
    ): PhoneSyncOutcome = run(only = only, pollCommands = pollCommands)

    private suspend fun run(
        only: Set<PhoneScope>?,
        pollCommands: Boolean,
    ): PhoneSyncOutcome = withContext(Dispatchers.IO) {
        val enabled = prefs.enabledScopes()
        if (!prefs.masterEnabled || enabled.isEmpty()) {
            return@withContext PhoneSyncOutcome(skipped = true)
        }
        val scopes = only?.intersect(enabled) ?: enabled
        // A commands-only tick has no scope work but still needs a round trip.
        if (only != null && scopes.isEmpty() && !pollCommands) {
            return@withContext PhoneSyncOutcome(skipped = true)
        }
        val api = apiProvider() ?: return@withContext PhoneSyncOutcome(skipped = true)

        val uploaded = mutableSetOf<PhoneScope>()
        val failed = mutableSetOf<PhoneScope>()
        val errors = mutableListOf<String>()
        var accepted = 0

        try {
            val deviceId = deviceId(api, enabled)

            suspend fun step(scope: PhoneScope, block: suspend () -> Int) {
                try {
                    accepted += block()
                    uploaded += scope
                } catch (e: Exception) {
                    if (stopsRun(e)) throw e
                    failed += scope
                    errors += AppStrings.get(
                        R.string.phone_message_scope_blocked,
                        context.getString(scope.titleRes),
                        e.friendlyMessage(),
                    )
                }
            }

            for (scope in PhoneScope.entries) {
                if (scope !in scopes) continue
                step(scope) { upload(scope, api, deviceId, enabled) }
            }

            // A full run refreshes the device row (its scope list and current
            // state) even when the Device info scope itself is switched off.
            if (only == null && PhoneScope.DEVICE !in scopes) {
                step(PhoneScope.DEVICE) { uploadDevice(api, deviceId, enabled) }
            }

            val commandCount = if (pollCommands) {
                try {
                    runCommands(api, deviceId).also {
                        prefs.lastCommandPollAt = System.currentTimeMillis()
                    }
                } catch (e: UnauthorizedException) {
                    throw e
                } catch (e: Exception) {
                    errors += AppStrings.get(
                        R.string.phone_message_commands_failed,
                        e.friendlyMessage(),
                    )
                    0
                }
            } else {
                0
            }

            prefs.lastSyncAt = System.currentTimeMillis()
            prefs.lastError = errors.joinToString("; ")
            prefs.lastResult = buildString {
                append(
                    context.resources.getQuantityString(
                        R.plurals.phone_synced_records,
                        accepted,
                        accepted,
                    ),
                )
                if (commandCount > 0) {
                    append(' ')
                    append(
                        context.resources.getQuantityString(
                            R.plurals.phone_synced_commands,
                            commandCount,
                            commandCount,
                        ),
                    )
                }
            }
            if (only == null) {
                // A full run just uploaded every scope; align the change
                // detector so the next loop tick does not upload them again.
                PhoneChangeDetector(context, prefs).commitCurrent(
                    scopes = enabled,
                    now = System.currentTimeMillis(),
                    failed = failed,
                )
                PhoneSyncScheduler.configure(context, prefs.intervalMinutes)
            }
            PhoneSyncOutcome(
                ok = errors.isEmpty(),
                accepted = accepted,
                commands = commandCount,
                errors = errors,
                uploadedScopes = uploaded,
                failedScopes = failed,
            )
        } catch (e: UnauthorizedException) {
            prefs.lastError = AppStrings.get(R.string.phone_message_session_expired)
            PhoneSyncOutcome(unauthorized = true)
        } catch (e: ApiException) {
            if (e.statusCode == 404) {
                // The device row is gone (revoked from another client). Drop
                // the id so the next run registers a fresh one.
                prefs.deviceId = null
            }
            prefs.lastError = e.friendlyMessage()
            PhoneSyncOutcome(
                retry = e.statusCode == 404,
                errors = listOf(e.friendlyMessage()),
            )
        } catch (e: IOException) {
            prefs.lastError = e.friendlyMessage()
            PhoneSyncOutcome(retry = true, errors = listOf(e.friendlyMessage()))
        }
    }

    private suspend fun upload(
        scope: PhoneScope,
        api: WinnieApi,
        deviceId: String,
        enabled: Set<PhoneScope>,
    ): Int = when (scope) {
        PhoneScope.DEVICE -> uploadDevice(api, deviceId, enabled)
        PhoneScope.CALENDAR -> uploadCalendar(api, deviceId)
        PhoneScope.CALLS -> uploadPaged(
            api = api,
            deviceId = deviceId,
            kind = "call",
            serializer = PhoneCallItem.serializer(),
            idOf = { it.externalId.toLongOrNull() ?: 0L },
            readPage = { cursor, limit -> callSource.readPage(cursor, limit) },
        )
        PhoneScope.MESSAGES -> uploadPaged(
            api = api,
            deviceId = deviceId,
            kind = "message",
            serializer = PhoneMessageItem.serializer(),
            idOf = { it.externalId.toLongOrNull() ?: 0L },
            readPage = { cursor, limit -> smsSource.readPage(cursor, limit) },
        )
        PhoneScope.CONTACTS -> uploadSnapshot(
            api = api,
            deviceId = deviceId,
            kind = "contact",
            items = contactsSource.readAll(),
            serializer = PhoneContactItem.serializer(),
            idOf = { it.externalId },
        )
        PhoneScope.APPS -> uploadSnapshot(
            api = api,
            deviceId = deviceId,
            kind = "app",
            items = appSource.readAll(),
            serializer = PhoneAppItem.serializer(),
            idOf = { it.externalId },
        )
        PhoneScope.NOTIFICATIONS -> uploadNotifications(api, deviceId)
        PhoneScope.LOCATION -> uploadLocation(api, deviceId)
    }

    // -- device ----------------------------------------------------------

    /**
     * Registers this phone if it has no device id yet, without syncing data.
     * The push service needs an id to receive device-targeted control events.
     */
    suspend fun ensureRegisteredDevice(): PhoneDevice? {
        val api = apiProvider() ?: return null
        val stored = prefs.deviceId
        if (stored != null) {
            try {
                return api.phoneDevice(stored)
            } catch (e: ApiException) {
                if (e.statusCode != 404) return null
                prefs.deviceId = null
            } catch (_: Exception) {
                return null
            }
        }
        return runCatching { registerDevice(api, prefs.enabledScopes()) }.getOrNull()
    }

    /**
     * The id every upload is addressed to. No device lookup on the hot path: a
     * revoked device answers 404, the run clears the id, and the next run
     * re-registers.
     */
    private suspend fun deviceId(api: WinnieApi, scopes: Set<PhoneScope>): String {
        prefs.deviceId?.let { return it }
        return registerDevice(api, scopes).id
    }

    private suspend fun registerDevice(api: WinnieApi, scopes: Set<PhoneScope>): PhoneDevice {
        val keys = scopes.map { it.key }.sorted()
        val device = api.registerPhoneDevice(deviceSource.registration(prefs.deviceKey, keys))
        prefs.deviceId = device.id
        return device
    }

    private suspend fun uploadDevice(
        api: WinnieApi,
        deviceId: String,
        scopes: Set<PhoneScope>,
    ): Int {
        val snapshot = deviceSource.snapshot(scopes.map { it.key }.sorted())
        val result = api.syncPhone(
            deviceId,
            PhoneSyncRequest(kind = "device", mode = "full", device = snapshot),
        )
        prefs.intervalMinutes = result.intervalMinutes
        return result.accepted
    }

    // -- snapshot kinds --------------------------------------------------

    private suspend fun uploadCalendar(api: WinnieApi, deviceId: String): Int {
        val now = System.currentTimeMillis()
        val since = PhoneSyncWindow.from(now)
        val until = PhoneSyncWindow.to(now)
        val items = calendarSource.readWindow(since, until)
        return uploadSnapshot(
            api = api,
            deviceId = deviceId,
            kind = "calendar",
            items = items,
            serializer = PhoneCalendarItem.serializer(),
            idOf = { it.externalId },
            window = since to until,
        )
    }

    private suspend fun <T> uploadSnapshot(
        api: WinnieApi,
        deviceId: String,
        kind: String,
        items: List<T>,
        serializer: KSerializer<T>,
        idOf: (T) -> String,
        window: Pair<Long, Long>? = null,
    ): Int {
        // A full snapshot is an authoritative delete list. A read that returns
        // nothing although the last upload had rows is far more likely to be a
        // provider that was not really read (a revoked grant, or a ROM that
        // answers "blank data" while keeping the grant) than every row having
        // been deleted on the phone. Skip it and say so, instead of telling the
        // server to erase the device's copy. The escape hatch is deliberate:
        // delete the server copy from the phone-data screen first, which clears
        // this counter, then sync.
        val previous = prefs.lastUploadCount(kind)
        if (refusesEmptySnapshot(items.size, previous)) {
            throw EmptySnapshotRead(
                kind = kind,
                previous = previous,
                message = AppStrings.get(R.string.phone_message_empty_read, previous),
            )
        }
        var accepted = 0
        if (items.size <= BATCH) {
            val result = api.syncPhone(
                deviceId,
                request(kind, "full", items, serializer, window = window),
            )
            prefs.intervalMinutes = result.intervalMinutes
            prefs.setLastUploadCount(kind, items.size)
            return result.accepted
        }

        items.chunked(BATCH).forEach { chunk ->
            val result = api.syncPhone(
                deviceId,
                request(kind, "incremental", chunk, serializer),
            )
            prefs.intervalMinutes = result.intervalMinutes
            accepted += result.accepted
        }
        val known = if (window != null) {
            api.phoneDeviceIds(
                deviceId,
                kind,
                Instant.ofEpochMilli(window.first).toString(),
                Instant.ofEpochMilli(window.second).toString(),
            )
        } else {
            api.phoneDeviceIds(deviceId, kind)
        }
        val current = items.map(idOf).toSet()
        known.filterNot { it in current }.chunked(BATCH).forEach { removed ->
            val result = api.syncPhone(
                deviceId,
                PhoneSyncRequest(kind = kind, deleted = removed),
            )
            accepted += result.deleted
        }
        prefs.setLastUploadCount(kind, items.size)
        return accepted
    }

    // -- cursor kinds ----------------------------------------------------

    private suspend fun <T> uploadPaged(
        api: WinnieApi,
        deviceId: String,
        kind: String,
        serializer: KSerializer<T>,
        idOf: (T) -> Long,
        readPage: (Long, Int) -> List<T>,
    ): Int {
        var cursor = prefs.cursor(kind).toLongOrNull() ?: 0L
        var accepted = 0
        while (true) {
            val page = readPage(cursor, BATCH)
            if (page.isEmpty()) break
            val result = api.syncPhone(
                deviceId,
                request(kind, "incremental", page, serializer, cursor = cursor.toString()),
            )
            accepted += result.accepted
            prefs.intervalMinutes = result.intervalMinutes
            val next = page.maxOf(idOf)
            if (next <= cursor) break
            cursor = next
            prefs.setCursor(kind, cursor.toString())
            if (page.size < BATCH) break
        }
        return accepted
    }

    // -- ambient kinds ---------------------------------------------------

    private suspend fun uploadNotifications(api: WinnieApi, deviceId: String): Int {
        var accepted = 0
        while (true) {
            val items = notificationBuffer.peek(BATCH)
            if (items.isEmpty()) break
            val result = api.syncPhone(
                deviceId,
                request(
                    "notification",
                    "incremental",
                    items,
                    PhoneNotificationItem.serializer(),
                ),
            )
            notificationBuffer.ack(items.size)
            accepted += result.accepted
            prefs.intervalMinutes = result.intervalMinutes
            if (items.size < BATCH) break
        }
        return accepted
    }

    private suspend fun uploadLocation(api: WinnieApi, deviceId: String): Int {
        val fix = locationSource.capture() ?: return 0
        val result = api.syncPhone(
            deviceId,
            request("location", "incremental", listOf(fix), PhoneLocationItem.serializer()),
        )
        return result.accepted
    }

    // -- commands --------------------------------------------------------

    private suspend fun runCommands(api: WinnieApi, deviceId: String): Int {
        val pending = api.pendingPhoneCommands(deviceId)
        for (command in pending) {
            val outcome = commandRunner.run(command)
            try {
                api.reportPhoneCommand(
                    deviceId,
                    command.id,
                    PhoneCommandResult(
                        status = outcome.status,
                        result = outcome.result,
                        error = outcome.error,
                    ),
                )
            } catch (_: Exception) {
                // The command already ran. The next sync will not re-issue a
                // dispatched command, so a failed report is logged in the UI as
                // a missing result rather than retried blindly.
            }
        }
        return pending.size
    }

    // -- encoding --------------------------------------------------------

    private fun <T> request(
        kind: String,
        mode: String,
        items: List<T>,
        serializer: KSerializer<T>,
        cursor: String = "",
        window: Pair<Long, Long>? = null,
    ): PhoneSyncRequest = PhoneSyncRequest(
        kind = kind,
        mode = mode,
        cursor = cursor,
        items = JsonArray(
            items.map { WinnieApi.json.encodeToJsonElement(serializer, it) }
        ),
        since = window?.let { Instant.ofEpochMilli(it.first).toString() },
        until = window?.let { Instant.ofEpochMilli(it.second).toString() },
    )

    private companion object {
        const val BATCH = 200
    }
}

/**
 * Whether an exception ends the whole run instead of just one scope.
 *
 * A transport failure, a dead session or a revoked device ends the run: the
 * rest cannot succeed. Any other HTTP error does not — the server answered, so
 * it is one scope's problem and the remaining scopes must still upload.
 *
 * The ordering is the point. `ApiException` extends `IOException`, so a plain
 * `catch (e: IOException) throw e` classifies every 4xx/5xx as a dead network
 * and aborts the run. That is how one calendar row the server rejected kept
 * calls, SMS, contacts, apps, notifications and location from ever being
 * attempted.
 */
internal fun stopsRun(error: Throwable): Boolean = when (error) {
    is CancellationException -> true
    is UnauthorizedException -> true
    is ApiException -> error.statusCode == 404
    is IOException -> true
    else -> false
}

/**
 * Whether a snapshot upload must be refused because the read came back empty
 * although this device had rows of that kind before.
 *
 * The counts come from the last successful upload of the same kind, so a phone
 * that has never synced that kind (previous = 0) still sends its first empty
 * snapshot. The refusal is a data-loss guard: a full snapshot is an
 * authoritative delete list, and a provider that answers "no rows" without
 * failing is a far more common cause than the owner deleting everything.
 */
internal fun refusesEmptySnapshot(items: Int, previous: Int): Boolean =
    items == 0 && previous > 0

/**
 * A full snapshot read came back empty although this device had uploaded rows
 * of that kind before.
 *
 * It is an exception rather than a silent skip so the run records it against
 * its own scope (and the phone-data screen shows it) while every other scope
 * still uploads. It is not an `IOException`, so [stopsRun] lets the run go on.
 */
internal class EmptySnapshotRead(
    val kind: String,
    val previous: Int,
    message: String,
) : Exception(message)
