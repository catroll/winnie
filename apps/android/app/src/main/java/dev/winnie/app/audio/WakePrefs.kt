package dev.winnie.app.audio

import android.content.Context
import androidx.core.content.edit

/**
 * Whether the phone listens for the wake word.
 *
 * Off by default and stored separately from the push settings: the wake word
 * holds the microphone, which is a different cost and a different privacy
 * decision from holding a network connection. Switching it off must stop the
 * service immediately, so the setter also drives the service.
 */
class WakePrefs(context: Context) {

    private val prefs = context.applicationContext.getSharedPreferences(
        PREFS_NAME,
        Context.MODE_PRIVATE,
    )

    var enabled: Boolean
        get() = prefs.getBoolean(KEY_ENABLED, false)
        set(value) {
            prefs.edit { putBoolean(KEY_ENABLED, value) }
        }

    /** The word the model listens for, shown in settings and the notification. */
    val keyword: String
        get() = prefs.getString(KEY_KEYWORD, null)?.takeIf { it.isNotBlank() } ?: DEFAULT_KEYWORD

    private companion object {
        const val PREFS_NAME = "winnie_wake"
        const val KEY_ENABLED = "enabled"
        const val KEY_KEYWORD = "keyword"
        const val DEFAULT_KEYWORD = "winnie"
    }
}
