package dev.winnie.app.data

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Encrypts one short secret before it is written to disk.
 *
 * The Android implementation keeps the AES key in the Keystore, so a copy of the
 * app's private data directory is not enough to recover the secret. Tests inject
 * a fake: Robolectric has no AndroidKeyStore provider, and the persistence rules
 * are what those tests are about.
 */
interface SecretBox {
    fun seal(plain: String): String
    fun open(payload: String): String?
}

/**
 * AES/GCM with a key that never leaves the Android Keystore.
 *
 * The payload is `base64(iv):base64(ciphertext)`; the IV must be stored because
 * GCM needs the original one to decrypt. A key rotation, a cleared Keystore or a
 * corrupted payload makes [open] return null rather than throw: losing a saved
 * secret must send the owner back to the login field, never crash the app.
 */
class KeystoreSecretBox : SecretBox {

    override fun seal(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val encrypted = cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return buildString {
            append(Base64.getEncoder().encodeToString(cipher.iv))
            append(':')
            append(Base64.getEncoder().encodeToString(encrypted))
        }
    }

    override fun open(payload: String): String? {
        val parts = payload.split(':', limit = 2)
        if (parts.size != 2) return null
        return try {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(
                Cipher.DECRYPT_MODE,
                secretKey(),
                GCMParameterSpec(GCM_TAG_BITS, Base64.getDecoder().decode(parts[0])),
            )
            String(cipher.doFinal(Base64.getDecoder().decode(parts[1])), Charsets.UTF_8)
        } catch (_: Exception) {
            null
        }
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
        // The token used this alias before the credentials store existed; keep
        // it so an app update does not invalidate the saved session.
        const val KEY_ALIAS = "winnie_token_key"
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val GCM_TAG_BITS = 128
    }
}
