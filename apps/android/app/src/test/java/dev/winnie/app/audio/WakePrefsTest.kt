package dev.winnie.app.audio

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class WakePrefsTest {

    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun listeningIsOffUntilTheOwnerAsksForIt() {
        val prefs = WakePrefs(context)
        assertFalse(prefs.enabled)
        prefs.enabled = true
        assertTrue(WakePrefs(context).enabled)
    }

    @Test
    fun theKeywordIsWinnieByDefault() {
        assertEquals("winnie", WakePrefs(context).keyword)
    }

    @Test
    fun switchingItOffIsStoredImmediately() {
        val prefs = WakePrefs(context)
        prefs.enabled = true
        prefs.enabled = false
        assertFalse(WakePrefs(context).enabled)
    }
}
