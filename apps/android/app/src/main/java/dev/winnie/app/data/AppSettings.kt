package dev.winnie.app.data

import android.content.Context
import androidx.core.content.edit
import dev.winnie.app.BuildConfig

/**
 * Non-secret client settings: the Winnie server address, the open chat, and how
 * a reply is read aloud.
 */
class AppSettings(context: Context) {

    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString(KEY_SERVER_URL, null)?.takeIf { it.isNotBlank() }
            ?: DEFAULT_SERVER_URL
        set(value) {
            prefs.edit { putString(KEY_SERVER_URL, normalizeServerUrl(value)) }
        }

    /**
     * The conversation the chat tab last showed. Reopening the app resumes it
     * instead of starting from an empty screen; the transcript itself stays on
     * the server, this is only the id.
     */
    var lastConversationId: String?
        get() = prefs.getString(KEY_LAST_CONVERSATION, null)?.takeIf { it.isNotBlank() }
        set(value) {
            prefs.edit {
                if (value.isNullOrBlank()) {
                    remove(KEY_LAST_CONVERSATION)
                } else {
                    putString(KEY_LAST_CONVERSATION, value)
                }
            }
        }

    /**
     * Whether and how a reply is read aloud.
     *
     * This stays on the device rather than on the server: the voices belong to
     * the phone's own speech engine, so the same choice made on a second phone
     * would name a voice that does not exist there.
     */
    var voiceOutput: VoiceOutput
        get() = VoiceOutput(
            enabled = prefs.getBoolean(KEY_VOICE_ENABLED, true),
            provider = prefs.getString(KEY_VOICE_PROVIDER, null)
                ?.takeIf { it.isNotBlank() }
                ?: VoiceOutput.SYSTEM,
            voice = prefs.getString(KEY_VOICE_NAME, null).orEmpty(),
            rate = prefs.getFloat(KEY_VOICE_RATE, 1f),
        ).normalized()
        set(value) {
            val normalized = value.normalized()
            prefs.edit {
                putBoolean(KEY_VOICE_ENABLED, normalized.enabled)
                putString(KEY_VOICE_PROVIDER, normalized.provider)
                putString(KEY_VOICE_NAME, normalized.voice)
                putFloat(KEY_VOICE_RATE, normalized.rate)
            }
        }

    private companion object {
        const val PREFS_NAME = "winnie_settings"
        const val KEY_SERVER_URL = "server_url"
        const val KEY_LAST_CONVERSATION = "last_conversation_id"
        const val KEY_VOICE_ENABLED = "voice_output_enabled"
        const val KEY_VOICE_PROVIDER = "voice_output_provider"
        const val KEY_VOICE_NAME = "voice_output_name"
        const val KEY_VOICE_RATE = "voice_output_rate"
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
