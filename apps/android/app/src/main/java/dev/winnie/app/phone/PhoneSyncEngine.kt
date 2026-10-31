package dev.winnie.app.phone

import android.content.Context
import dev.winnie.app.data.ApiException
import dev.winnie.app.data.PhoneCalendarItem
import dev.winnie.app.data.PhoneCallItem
import dev.winnie.app.data.PhoneCommandResult
import dev.winnie.app.data.PhoneContactItem
import dev.winnie.app.data.PhoneAppItem
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
)

/**
 * One sync run: register the device if needed, upload every enabled scope, then
 * execute confirmed commands.
 *
 * Scopes are isolated: a provider that throws (a vendor ROM refusing a column,
 * a revoked permission) records an error and the other scopes still upload.
 * Only an expired session or a transport failure stops the run.
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

    suspend fun syncNow(): PhoneSyncOutcome = withContext(Dispatchers.IO) {
        val scopes = prefs.enabledScopes()
        if (!prefs.masterEnabled || scopes.isEmpty()) {
            return@withContext PhoneSyncOutcome(skipped = true)
        }
        val api = apiProvider() ?: return@withContext PhoneSyncOutcome(skipped = true)

        try {
            val device = ensureDevice(api, scopes)
            var accepted = 0
            val errors = mutableListOf<String>()

            suspend fun step(label: String, block: suspend () -> Int) {
                try {
                    accepted += block()
                } catch (e: Exception) {
                    if (stopsRun(e)) throw e
                    errors += "$label: ${e.friendlyMessage()}"
                }
            }

            step("device") { uploadDevice(api, device.id, scopes) }
            if (PhoneScope.CALENDAR in scopes) {
                step("calendar") { uploadCalendar(api, device.id) }
            }
            if (PhoneScope.CALLS in scopes) {
                step("call") {
                    uploadPaged(
                        api = api,
                        deviceId = device.id,
                        kind = "call",
                        serializer = PhoneCallItem.serializer(),
                        idOf = { it.externalId.toLongOrNull() ?: 0L },
                        readPage = { cursor, limit -> callSource.readPage(cursor, limit) },
                    )
                }
            }
            if (PhoneScope.MESSAGES in scopes) {
                step("message") {
                    uploadPaged(
                        api = api,
                        deviceId = device.id,
                        kind = "message",
                        serializer = PhoneMessageItem.serializer(),
                        idOf = { it.externalId.toLongOrNull() ?: 0L },
                        readPage = { cursor, limit -> smsSource.readPage(cursor, limit) },
                    )
                }
            }
            if (PhoneScope.CONTACTS in scopes) {
                step("contact") {
                    uploadSnapshot(
                        api = api,
                        deviceId = device.id,
                        kind = "contact",
                        items = contactsSource.readAll(),
                        serializer = PhoneContactItem.serializer(),
                        idOf = { it.externalId },
                    )
                }
            }
            if (PhoneScope.APPS in scopes) {
                step("app") {
                    uploadSnapshot(
                        api = api,
                        deviceId = device.id,
                        kind = "app",
                        items = appSource.readAll(),
                        serializer = PhoneAppItem.serializer(),
                        idOf = { it.externalId },
                    )
                }
            }
            if (PhoneScope.NOTIFICATIONS in scopes) {
                step("notification") { uploadNotifications(api, device.id) }
            }
            if (PhoneScope.LOCATION in scopes) {
                step("location") { uploadLocation(api, device.id) }
            }

            val commandCount = try {
                runCommands(api, device.id)
            } catch (e: UnauthorizedException) {
                throw e
            } catch (e: Exception) {
                errors += "commands: ${e.friendlyMessage()}"
                0
            }

            prefs.lastSyncAt = System.currentTimeMillis()
            prefs.lastError = errors.joinToString("; ")
            prefs.lastResult = buildString {
                append(accepted).append(" records")
                if (commandCount > 0) append(", ").append(commandCount).append(" commands")
            }
            PhoneSyncScheduler.configure(context, prefs.intervalMinutes)
            PhoneSyncOutcome(
                ok = errors.isEmpty(),
                accepted = accepted,
                commands = commandCount,
                errors = errors,
            )
        } catch (e: UnauthorizedException) {
            prefs.lastError = "The session expired. Sign in again to resume syncing."
            PhoneSyncOutcome(unauthorized = true)
        } catch (e: ApiException) {
            // The server answered before any scope ran — registration or the
            // device lookup. Retrying will not change that, so report it.
            prefs.lastError = e.friendlyMessage()
            PhoneSyncOutcome(errors = listOf(e.friendlyMessage()))
        } catch (e: IOException) {
            prefs.lastError = e.friendlyMessage()
            PhoneSyncOutcome(retry = true, errors = listOf(e.friendlyMessage()))
        }
    }

    // -- device ----------------------------------------------------------

    /**
     * Registers this phone if it has no device id yet, without syncing data.
     * The push service needs an id to receive device-targeted control events.
     */
    suspend fun ensureRegisteredDevice(): dev.winnie.app.data.PhoneDevice? {
        val api = apiProvider() ?: return null
        return runCatching {
            ensureDevice(api, prefs.enabledScopes())
        }.getOrNull()
    }

    private suspend fun ensureDevice(
        api: WinnieApi,
        scopes: Set<PhoneScope>,
    ): dev.winnie.app.data.PhoneDevice {
        val stored = prefs.deviceId
        if (stored != null) {
            try {
                return api.phoneDevice(stored)
            } catch (e: ApiException) {
                if (e.statusCode != 404) throw e
            }
        }
        val keys = scopes.map { it.key }.sorted()
        val device = api.registerPhoneDevice(
            deviceSource.registration(prefs.deviceKey, keys)
        )
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
        val since = now - CALENDAR_BACK_DAYS * DAY_MS
        val until = now + CALENDAR_AHEAD_DAYS * DAY_MS
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
        var accepted = 0
        if (items.size <= BATCH) {
            val result = api.syncPhone(
                deviceId,
                request(kind, "full", items, serializer, window = window),
            )
            prefs.intervalMinutes = result.intervalMinutes
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
        const val CALENDAR_BACK_DAYS = 180L
        const val CALENDAR_AHEAD_DAYS = 365L
        const val DAY_MS = 24L * 60L * 60L * 1000L
    }
}

/**
 * Whether an exception ends the whole run instead of just one scope.
 *
 * A transport failure or a dead session stops the run: the rest cannot succeed.
 * An HTTP error does not — the server answered, so it is one scope's problem
 * and the remaining scopes must still upload.
 *
 * The ordering is the point. `ApiException` extends `IOException`, so a plain
 * `catch (e: IOException) throw e` — which this used to be — classifies every
 * 4xx/5xx as a dead network and aborts the run. That is how one calendar row
 * the server rejected kept calls, SMS, contacts, apps, notifications and
 * location from ever being attempted.
 */
internal fun stopsRun(error: Throwable): Boolean = when (error) {
    is CancellationException -> true
    is UnauthorizedException -> true
    is ApiException -> false
    is IOException -> true
    else -> false
}
