package dev.winnie.app.data

import android.content.Context
import androidx.core.content.edit

/** The sign-in name and password the owner asked this device to remember. */
data class SavedCredentials(
    val username: String,
    val password: String,
)

/**
 * Remembers the login form on this device.
 *
 * The password is sealed with the Android Keystore key from [SecretBox], not
 * stored in clear text, so the file is useless off the device. That is still a
 * deliberate trade: anyone who can unlock the phone and open Winnie can read the
 * saved password through the eye toggle, which is why the login screen carries
 * an explicit *Remember password* switch and the account screen lets the owner
 * forget it again. Unchecking the switch deletes both values immediately.
 */
class CredentialStore(
    context: Context,
    private val box: SecretBox = KeystoreSecretBox(),
) {

    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    /** Whether the owner opted in. Independent from whether a value decrypted. */
    val remember: Boolean
        get() = prefs.getBoolean(KEY_REMEMBER, false)

    /**
     * Whether the owner ever answered the remember question. The login screen
     * defaults to on for a fresh install, but must not turn itself back on after
     * the owner deliberately unchecked it.
     */
    val hasChoice: Boolean
        get() = prefs.contains(KEY_REMEMBER)

    /** The remembered pair, or null when remembering is off or unreadable. */
    fun saved(): SavedCredentials? {
        if (!remember) return null
        val username = read(KEY_USERNAME) ?: return null
        val password = read(KEY_PASSWORD) ?: return null
        return SavedCredentials(username, password)
    }

    /**
     * Stores the pair and turns remembering on. Both values are written even if
     * one of them cannot be sealed, so a failure does not leave a half-filled
     * form on the next start.
     */
    fun save(username: String, password: String) {
        val sealedName = runCatching { box.seal(username) }.getOrNull() ?: return
        val sealedPassword = runCatching { box.seal(password) }.getOrNull() ?: return
        prefs.edit {
            putBoolean(KEY_REMEMBER, true)
            putString(KEY_USERNAME, sealedName)
            putString(KEY_PASSWORD, sealedPassword)
        }
    }

    /** Forgets both values and turns remembering off. */
    fun clear() {
        prefs.edit {
            remove(KEY_USERNAME)
            remove(KEY_PASSWORD)
            putBoolean(KEY_REMEMBER, false)
        }
    }

    private fun read(key: String): String? {
        val payload = prefs.getString(key, null) ?: return null
        val value = runCatching { box.open(payload) }.getOrNull()
        if (value == null) {
            // Unreadable means unusable: do not keep prompting for a value the
            // Keystore can never return again.
            prefs.edit { remove(key) }
        }
        return value
    }

    private companion object {
        const val PREFS_NAME = "winnie_credentials"
        const val KEY_REMEMBER = "remember"
        const val KEY_USERNAME = "username"
        const val KEY_PASSWORD = "password"
    }
}
