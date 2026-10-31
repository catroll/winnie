package dev.winnie.app.phone.sources

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import dev.winnie.app.data.PhoneNotificationItem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class NotificationBufferTest {

    private val context: Context = ApplicationProvider.getApplicationContext()
    private lateinit var buffer: NotificationBuffer

    @Before
    fun setUp() {
        buffer = NotificationBuffer(context)
        buffer.clear()
    }

    @Test
    fun appendPeekAndAckInOrder() {
        buffer.append(item("1", "first"))
        buffer.append(item("2", "second"))

        assertEquals(2, buffer.size())
        val peeked = buffer.peek(1)
        assertEquals(1, peeked.size)
        assertEquals("1", peeked.single().externalId)
        assertEquals("first", peeked.single().text)

        buffer.ack(1)

        assertEquals(1, buffer.size())
        assertEquals("2", buffer.peek(10).single().externalId)
    }

    @Test
    fun ackMoreThanHeldLeavesAnEmptyBuffer() {
        buffer.append(item("1", "only"))

        buffer.ack(5)

        assertEquals(0, buffer.size())
        assertTrue(buffer.peek(10).isEmpty())
    }

    @Test
    fun survivesAnotherInstance() {
        buffer.append(item("1", "persisted"))

        val reopened = NotificationBuffer(context)

        assertEquals("persisted", reopened.peek(10).single().text)
    }

    private fun item(id: String, text: String) = PhoneNotificationItem(
        externalId = id,
        packageName = "com.example",
        appLabel = "Health",
        title = "Reminder",
        text = text,
        postedAt = "2026-11-01T05:00:00Z",
    )
}
