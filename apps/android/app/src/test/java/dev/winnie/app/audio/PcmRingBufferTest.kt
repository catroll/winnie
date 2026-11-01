package dev.winnie.app.audio

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class PcmRingBufferTest {

    @Test
    fun `an empty buffer snapshots nothing`() {
        val ring = PcmRingBuffer(8)
        assertEquals(0, ring.size)
        assertArrayEquals(ByteArray(0), ring.snapshot())
    }

    @Test
    fun `bytes are returned oldest first`() {
        val ring = PcmRingBuffer(4)
        ring.append(byteArrayOf(1, 2, 3))
        assertArrayEquals(byteArrayOf(1, 2, 3), ring.snapshot())
    }

    @Test
    fun `a fill that wraps keeps the newest bytes in order`() {
        val ring = PcmRingBuffer(4)
        ring.append(byteArrayOf(1, 2, 3, 4, 5, 6))
        assertEquals(4, ring.size)
        assertArrayEquals(byteArrayOf(3, 4, 5, 6), ring.snapshot())
    }

    @Test
    fun `several wraps still read in time order`() {
        val ring = PcmRingBuffer(5)
        ring.append(byteArrayOf(1, 2))
        ring.append(byteArrayOf(3, 4))
        ring.append(byteArrayOf(5, 6, 7))
        assertArrayEquals(byteArrayOf(3, 4, 5, 6, 7), ring.snapshot())
    }

    @Test
    fun `an append exactly the capacity resets the read point`() {
        val ring = PcmRingBuffer(3)
        ring.append(byteArrayOf(1, 2, 3))
        assertArrayEquals(byteArrayOf(1, 2, 3), ring.snapshot())
        ring.append(byteArrayOf(4))
        assertArrayEquals(byteArrayOf(2, 3, 4), ring.snapshot())
    }

    @Test
    fun `clear empties the buffer`() {
        val ring = PcmRingBuffer(4)
        ring.append(byteArrayOf(9, 9, 9, 9))
        ring.clear()
        assertEquals(0, ring.size)
        assertArrayEquals(ByteArray(0), ring.snapshot())
    }

    @Test
    fun `a zero capacity buffer discards everything`() {
        val ring = PcmRingBuffer(0)
        ring.append(byteArrayOf(1, 2, 3))
        assertEquals(0, ring.size)
        assertArrayEquals(ByteArray(0), ring.snapshot())
    }
}
