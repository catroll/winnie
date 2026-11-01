package dev.winnie.app.phone

import android.content.Context
import androidx.core.content.edit
import java.util.UUID

/**
 * Client-side phone sync settings and cursors.
 *
 * The device key is generated once and kept across sign-ins: the server
 * upserts on it, so reinstalling the API token or wiping the server data does
 * not create a second device row. Cursors are the highest `_ID` the server has
 * already accepted for a kind; they live here rather than on the server because
 * only the phone knows how far it has read.
 */
class PhonePrefs(context: Context) {

    private val prefs =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    var masterEnabled: Boolean
        get() = prefs.getBoolean(KEY_MASTER, false)
        set(value) = prefs.edit { putBoolean(KEY_MASTER, value) }

    fun isEnabled(scope: PhoneScope): Boolean =
        prefs.getBoolean("$KEY_SCOPE_PREFIX${scope.key}", false)

    fun setEnabled(scope: PhoneScope, enabled: Boolean) {
        prefs.edit { putBoolean("$KEY_SCOPE_PREFIX${scope.key}", enabled) }
        if (!enabled) setCursor(scope.key, "")
    }

    fun enabledScopes(): Set<PhoneScope> =
        PhoneScope.entries.filter { isEnabled(it) }.toSet()

    /** A stable key for this installation; created on first use. */
    val deviceKey: String
        get() {
            prefs.getString(KEY_DEVICE_KEY, null)?.takeIf { it.isNotBlank() }?.let {
                return it
            }
            val created = UUID.randomUUID().toString()
            prefs.edit { putString(KEY_DEVICE_KEY, created) }
            return created
        }

    var deviceId: String?
        get() = prefs.getString(KEY_DEVICE_ID, null)?.takeIf { it.isNotBlank() }
        set(value) = prefs.edit {
            if (value.isNullOrBlank()) remove(KEY_DEVICE_ID) else putString(KEY_DEVICE_ID, value)
        }

    fun cursor(kind: String): String =
        prefs.getString("$KEY_CURSOR_PREFIX$kind", "") ?: ""

    fun setCursor(kind: String, value: String) =
        prefs.edit { putString("$KEY_CURSOR_PREFIX$kind", value) }

    fun resetCursors() {
        prefs.edit {
            PhoneScope.entries.forEach { remove("$KEY_CURSOR_PREFIX${it.key}") }
        }
    }

    var lastSyncAt: Long?
        get() = prefs.getLong(KEY_LAST_SYNC, 0L).takeIf { it > 0L }
        set(value) = prefs.edit {
            if (value == null) remove(KEY_LAST_SYNC) else putLong(KEY_LAST_SYNC, value)
        }

    var lastError: String
        get() = prefs.getString(KEY_LAST_ERROR, "") ?: ""
        set(value) = prefs.edit { putString(KEY_LAST_ERROR, value.take(2000)) }

    var lastResult: String
        get() = prefs.getString(KEY_LAST_RESULT, "") ?: ""
        set(value) = prefs.edit { putString(KEY_LAST_RESULT, value.take(500)) }

    var intervalMinutes: Int
        get() = prefs.getInt(KEY_INTERVAL, DEFAULT_INTERVAL_MINUTES)
        set(value) = prefs.edit {
            putInt(KEY_INTERVAL, value.coerceAtLeast(MIN_INTERVAL_MINUTES))
        }

    /**
     * The cheapest "did anything change?" token for a kind, as of its last
     * sync. The 5-second loop only computes it again when a check is due, and
     * only uploads when it differs — which is what keeps a 5-second cadence
     * from turning into a 5-second upload.
     */
    fun fingerprint(kind: String): String =
        prefs.getString("$KEY_FINGERPRINT_PREFIX$kind", "") ?: ""

    fun setFingerprint(kind: String, value: String) =
        prefs.edit { putString("$KEY_FINGERPRINT_PREFIX$kind", value.take(1000)) }

    /** When this kind was last checked locally, changed or not. */
    fun lastCheckAt(kind: String): Long =
        prefs.getLong("$KEY_CHECK_PREFIX$kind", 0L)

    fun setLastCheckAt(kind: String, value: Long) =
        prefs.edit { putLong("$KEY_CHECK_PREFIX$kind", value) }

    /**
     * How many rows the last successful snapshot upload of [kind] carried.
     *
     * A full snapshot replaces the server's rows for that kind, so an empty one
     * is only sent when this is also zero: otherwise a provider that answers
     * with no rows (a revoked grant, a ROM that blanks the data while keeping
     * the grant) would silently delete everything the phone had synced.
     */
    fun lastUploadCount(kind: String): Int =
        prefs.getInt("$KEY_UPLOADED_COUNT_PREFIX$kind", 0)

    fun setLastUploadCount(kind: String, value: Int) =
        prefs.edit { putInt("$KEY_UPLOADED_COUNT_PREFIX$kind", value) }

    /** When the phone last asked the server for pending commands. */
    var lastCommandPollAt: Long
        get() = prefs.getLong(KEY_LAST_COMMAND_POLL, 0L)
        set(value) = prefs.edit { putLong(KEY_LAST_COMMAND_POLL, value) }

    /** Wipes what a full re-upload needs to be scheduled again. */
    fun resetSyncState() {
        prefs.edit {
            PhoneScope.entries.forEach {
                remove("$KEY_FINGERPRINT_PREFIX${it.key}")
                remove("$KEY_CHECK_PREFIX${it.key}")
                remove("$KEY_UPLOADED_COUNT_PREFIX${it.key}")
            }
            remove(KEY_LAST_COMMAND_POLL)
        }
    }

    private companion object {
        const val PREFS_NAME = "winnie_phone"
        const val KEY_MASTER = "master_enabled"
        const val KEY_SCOPE_PREFIX = "scope_"
        const val KEY_DEVICE_KEY = "device_key"
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_CURSOR_PREFIX = "cursor_"
        const val KEY_FINGERPRINT_PREFIX = "fingerprint_"
        const val KEY_CHECK_PREFIX = "checked_"
        const val KEY_UPLOADED_COUNT_PREFIX = "uploaded_"
        const val KEY_LAST_COMMAND_POLL = "last_command_poll_at"
        const val KEY_LAST_SYNC = "last_sync_at"
        const val KEY_LAST_ERROR = "last_error"
        const val KEY_LAST_RESULT = "last_result"
        const val KEY_INTERVAL = "interval_minutes"
        const val DEFAULT_INTERVAL_MINUTES = 30
        const val MIN_INTERVAL_MINUTES = 15
    }
}
