package dev.winnie.app.data

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * The persistence rules around remembering a password. A fake [SecretBox] stands
 * in for the Keystore, which Robolectric does not implement; the point of these
 * tests is what is written, what is cleared and what an unreadable payload does,
 * not the AES implementation itself.
 */
@RunWith(RobolectricTestRunner::class)
class CredentialStoreTest {

    private class FakeBox : SecretBox {
        override fun seal(plain: String): String = PREFIX + plain

        override fun open(payload: String): String? =
            payload.removePrefix(PREFIX).takeIf { payload.startsWith(PREFIX) }

        private companion object {
            const val PREFIX = "sealed:"
        }
    }

    private lateinit var context: Context
    private lateinit var store: CredentialStore

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        // Start each test from a fresh install: an explicit clear() would itself
        // count as the owner having answered the remember question.
        context.getSharedPreferences("winnie_credentials", Context.MODE_PRIVATE)
            .edit()
            .clear()
            .commit()
        store = CredentialStore(context, FakeBox())
    }

    @Test
    fun aFreshInstallHasNotChosenYet() {
        assertFalse(store.hasChoice)
        assertFalse(store.remember)
        assertNull(store.saved())
    }

    @Test
    fun savingTurnsRememberingOnAndSealsBothValues() {
        store.save("alice", "correct horse")

        assertTrue(store.hasChoice)
        assertTrue(store.remember)
        assertEquals(SavedCredentials("alice", "correct horse"), store.saved())

        val prefs = context.getSharedPreferences("winnie_credentials", Context.MODE_PRIVATE)
        // Neither value is written in clear text.
        assertEquals("sealed:alice", prefs.getString("username", null))
        assertEquals("sealed:correct horse", prefs.getString("password", null))
    }

    @Test
    fun clearingForgetsTheValuesButRemembersTheChoice() {
        store.save("alice", "correct horse")
        store.clear()

        assertTrue(store.hasChoice)
        assertFalse(store.remember)
        assertNull(store.saved())

        val prefs = context.getSharedPreferences("winnie_credentials", Context.MODE_PRIVATE)
        assertNull(prefs.getString("username", null))
        assertNull(prefs.getString("password", null))
    }

    @Test
    fun anUnreadablePayloadIsDroppedInsteadOfReturned() {
        store.save("alice", "correct horse")
        val prefs = context.getSharedPreferences("winnie_credentials", Context.MODE_PRIVATE)
        prefs.edit().putString("password", "not-a-sealed-payload").commit()

        assertNull(store.saved())
        // The unreadable value is removed so the form does not keep trying it.
        assertNull(prefs.getString("password", null))
    }
}
