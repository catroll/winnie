package dev.winnie.app.data

import android.content.Context
import androidx.core.content.edit

/**
 * Stores the bearer token encrypted with a key that never leaves the Android
 * Keystore. Plain `SharedPreferences` would expose the token to anyone able to
 * read the app's private data.
 */
class TokenStore(
    context: Context,
    private val box: SecretBox = KeystoreSecretBox(),
) {

    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun save(token: String) {
        val payload = runCatching { box.seal(token) }.getOrNull() ?: return
        prefs.edit { putString(KEY_TOKEN, payload) }
    }

    fun read(): String? {
        val payload = prefs.getString(KEY_TOKEN, null) ?: return null
        val token = runCatching { box.open(payload) }.getOrNull()
        if (token == null) {
            // A key rotation or a corrupted payload must not brick the app.
            prefs.edit { remove(KEY_TOKEN) }
        }
        return token
    }

    fun clear() {
        prefs.edit { remove(KEY_TOKEN) }
    }

    private companion object {
        const val PREFS_NAME = "winnie_secure"
        const val KEY_TOKEN = "access_token"
    }
}
