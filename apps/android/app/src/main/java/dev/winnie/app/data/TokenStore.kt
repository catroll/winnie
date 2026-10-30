package dev.winnie.app.data

import android.content.Context
import androidx.core.content.edit
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey

/**
 * Stores the bearer token encrypted with a key that never leaves the Android
 * Keystore. Plain `SharedPreferences` would expose the token to anyone able to
 * read the app's private data.
 *
 * The encrypted payload is `base64(iv):base64(ciphertext)`; both parts are
 * required to decrypt because AES/GCM needs the original IV.
 */
class TokenStore(context: Context) {

    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun save(token: String) {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val encrypted = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        val payload = buildString {
            append(Base64.getEncoder().encodeToString(cipher.iv))
            append(':')
            append(Base64.getEncoder().encodeToString(encrypted))
        }
        prefs.edit { putString(KEY_TOKEN, payload) }
    }

    fun read(): String? {
        val payload = prefs.getString(KEY_TOKEN, null) ?: return null
        return try {
            val (ivPart, cipherPart) = payload.split(':', limit = 2).let {
                if (it.size != 2) return null
                it[0] to it[1]
            }
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(
                Cipher.DECRYPT_MODE,
                secretKey(),
                javax.crypto.spec.GCMParameterSpec(
                    GCM_TAG_BITS,
                    Base64.getDecoder().decode(ivPart),
                ),
            )
            String(cipher.doFinal(Base64.getDecoder().decode(cipherPart)), Charsets.UTF_8)
        } catch (_: Exception) {
            // A key rotation or a corrupted payload must not brick the app.
            prefs.edit { remove(KEY_TOKEN) }
            null
        }
    }

    fun clear() {
        prefs.edit { remove(KEY_TOKEN) }
    }

    private fun secretKey(): SecretKey {
        val store = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (store.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry)?.let {
            return it.secretKey
        }
        val generator = KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES,
            ANDROID_KEYSTORE,
        )
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return generator.generateKey()
    }

    private companion object {
        const val PREFS_NAME = "winnie_secure"
        const val KEY_TOKEN = "access_token"
        const val KEY_ALIAS = "winnie_token_key"
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val GCM_TAG_BITS = 128
    }
}
