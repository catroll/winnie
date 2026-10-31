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

    private companion object {
        const val PREFS_NAME = "winnie_phone"
        const val KEY_MASTER = "master_enabled"
        const val KEY_SCOPE_PREFIX = "scope_"
        const val KEY_DEVICE_KEY = "device_key"
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_CURSOR_PREFIX = "cursor_"
        const val KEY_LAST_SYNC = "last_sync_at"
        const val KEY_LAST_ERROR = "last_error"
        const val KEY_LAST_RESULT = "last_result"
        const val KEY_INTERVAL = "interval_minutes"
        const val DEFAULT_INTERVAL_MINUTES = 30
        const val MIN_INTERVAL_MINUTES = 15
    }
}
