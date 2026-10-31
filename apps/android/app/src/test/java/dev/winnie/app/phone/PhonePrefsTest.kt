package dev.winnie.app.phone

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class PhonePrefsTest {

    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun defaultsToSharingNothing() {
        val prefs = PhonePrefs(context)

        assertFalse(prefs.masterEnabled)
        assertTrue(prefs.enabledScopes().isEmpty())
        assertEquals("", prefs.cursor("message"))
        assertNull(prefs.deviceId)
        assertNull(prefs.lastSyncAt)
        assertEquals(30, prefs.intervalMinutes)
    }

    @Test
    fun togglesScopesAndResetsTheirCursors() {
        val prefs = PhonePrefs(context)

        prefs.setEnabled(PhoneScope.MESSAGES, true)
        prefs.setEnabled(PhoneScope.CALLS, true)
        prefs.setCursor("message", "120")

        assertEquals(
            setOf(PhoneScope.MESSAGES, PhoneScope.CALLS),
            prefs.enabledScopes(),
        )
        assertEquals("120", prefs.cursor("message"))

        prefs.setEnabled(PhoneScope.MESSAGES, false)
        assertEquals("", prefs.cursor("message"))
        assertEquals(setOf(PhoneScope.CALLS), prefs.enabledScopes())

        prefs.resetCursors()
        assertEquals("", prefs.cursor("call"))
    }

    @Test
    fun deviceKeyIsStableAcrossInstances() {
        val first = PhonePrefs(context).deviceKey
        val second = PhonePrefs(context).deviceKey

        assertEquals(first, second)
        assertTrue(first.length >= 8)
        assertNotEquals("", first)
    }

    @Test
    fun intervalNeverDropsBelowTheWorkManagerMinimum() {
        val prefs = PhonePrefs(context)

        prefs.intervalMinutes = 5
        assertEquals(15, prefs.intervalMinutes)
        prefs.intervalMinutes = 60
        assertEquals(60, prefs.intervalMinutes)
    }
}
