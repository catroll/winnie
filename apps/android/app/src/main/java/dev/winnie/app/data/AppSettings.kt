package dev.winnie.app.data

import android.content.Context
import androidx.core.content.edit
import dev.winnie.app.BuildConfig

/** Non-secret client settings: the Winnie server address. */
class AppSettings(context: Context) {

    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, null)?.takeIf { it.isNotBlank() }
            ?: DEFAULT_SERVER_URL
        set(value) {
            prefs.edit { putString(KEY_SERVER_URL, normalizeServerUrl(value)) }
        }

    private companion object {
        const val PREFS_NAME = "winnie_settings"
        const val KEY_SERVER_URL = "server_url"
    }
}

/**
 * Baked in at build time from `WINNIE_PUBLIC_URL`. It falls back to `10.0.2.2`,
 * the host loopback address as seen from the Android emulator; the login
 * screen lets the user change it, and the value is persisted.
 */
val DEFAULT_SERVER_URL: String = BuildConfig.DEFAULT_SERVER_URL

fun normalizeServerUrl(raw: String): String {
    val trimmed = raw.trim().trimEnd('/')
    if (trimmed.isBlank()) return DEFAULT_SERVER_URL
    return if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
        trimmed
    } else {
        "http://$trimmed"
    }
}
