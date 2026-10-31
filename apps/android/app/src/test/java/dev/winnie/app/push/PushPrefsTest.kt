package dev.winnie.app.push

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import dev.winnie.app.data.PushEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class PushPrefsTest {

    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun defaultsToOffWithNoCursor() {
        val prefs = PushPrefs(context)

        assertFalse(prefs.enabled)
        assertEquals(0L, prefs.lastSeq)
        assertEquals("disconnected", prefs.status)
        assertEquals("", prefs.lastError)
        assertNull(prefs.lastEventAt)
    }

    @Test
    fun persistsTheCursorAndConnectionState() {
        val prefs = PushPrefs(context)
        prefs.enabled = true
        prefs.lastSeq = 42
        prefs.lastEventAt = 1_700_000_000_000L
        prefs.status = "connected"

        val reopened = PushPrefs(context)

        assertTrue(reopened.enabled)
        assertEquals(42L, reopened.lastSeq)
        assertEquals("connected", reopened.status)
        assertEquals(1_700_000_000_000L, reopened.lastEventAt)

        reopened.resetCursor()
        assertEquals(0L, reopened.lastSeq)
    }
}

@RunWith(RobolectricTestRunner::class)
class PushRoutingTest {

    @Test
    fun aSyncControlMessageRunsASync() {
        val event = PushEvent(type = "event", seq = 1, kind = "sync")

        assertEquals(PushAction.RunSync, pushActionFor(event))
    }

    @Test
    fun aNotificationIsShown() {
        val event = PushEvent(type = "event", seq = 2, title = "Hello", body = "World")

        val action = pushActionFor(event)

        assertTrue(action is PushAction.ShowNotification)
        assertEquals("Hello", (action as PushAction.ShowNotification).event.title)
    }

    @Test
    fun heartbeatsAndReadyFramesAreIgnored() {
        assertEquals(PushAction.Ignore, pushActionFor(PushEvent(type = "ping")))
        assertEquals(PushAction.Ignore, pushActionFor(PushEvent(type = "ready")))
    }

    @Test
    fun phoneCommandResultsAreUserVisible() {
        val event = PushEvent(
            type = "event",
            seq = 3,
            kind = "phone_command",
            title = "Phone action completed",
        )

        assertTrue(pushActionFor(event) is PushAction.ShowNotification)
    }
}
