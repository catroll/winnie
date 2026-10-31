package dev.winnie.app.push

import android.content.Context
import androidx.core.content.edit

/**
 * Persisted state for the background connection.
 *
 * `lastSeq` is the delivery cursor: the server replays events after it, so it
 * must be written before the event is shown. The connection state is mirrored
 * in shared preferences so the settings screen can report it after a process
 * restart, and kept in [PushStatusBus] for live updates.
 */
class PushPrefs(context: Context) {

    private val prefs =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    var enabled: Boolean
        get() = prefs.getBoolean(KEY_ENABLED, false)
        set(value) = prefs.edit { putBoolean(KEY_ENABLED, value) }

    var lastSeq: Long
        get() = prefs.getLong(KEY_LAST_SEQ, 0L)
        set(value) = prefs.edit { putLong(KEY_LAST_SEQ, value.coerceAtLeast(0L)) }

    var lastEventAt: Long?
        get() = prefs.getLong(KEY_LAST_EVENT, 0L).takeIf { it > 0L }
        set(value) = prefs.edit {
            if (value == null) remove(KEY_LAST_EVENT) else putLong(KEY_LAST_EVENT, value)
        }

    var status: String
        get() = prefs.getString(KEY_STATUS, "disconnected") ?: "disconnected"
        set(value) = prefs.edit { putString(KEY_STATUS, value.take(300)) }

    var lastError: String
        get() = prefs.getString(KEY_ERROR, "") ?: ""
        set(value) = prefs.edit { putString(KEY_ERROR, value.take(500)) }

    fun resetCursor() {
        prefs.edit { remove(KEY_LAST_SEQ) }
    }

    private companion object {
        const val PREFS_NAME = "winnie_push"
        const val KEY_ENABLED = "enabled"
        const val KEY_LAST_SEQ = "last_seq"
        const val KEY_LAST_EVENT = "last_event_at"
        const val KEY_STATUS = "status"
        const val KEY_ERROR = "last_error"
    }
}
