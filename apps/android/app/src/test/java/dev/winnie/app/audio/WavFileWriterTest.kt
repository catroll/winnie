package dev.winnie.app.audio

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Test

class WavFileWriterTest {

    @Test
    fun writesHeaderAndPatchesSizesOnClose() {
        val file = File.createTempFile("winnie-wav", ".wav")
        try {
            val samples = 1600 // 100 ms at 16 kHz mono 16-bit
            WavFileWriter(file, sampleRate = 16_000, channels = 1, bitsPerSample = 16).use {
                it.append(ByteArray(samples * 2) { index -> (index % 127).toByte() })
            }

            val bytes = file.readBytes()
            assertEquals(WavFileWriter.HEADER_BYTES + samples * 2, bytes.size)
            assertEquals("RIFF", ascii(bytes, 0, 4))
            assertEquals(36 + samples * 2, intLe(bytes, 4))
            assertEquals("WAVE", ascii(bytes, 8, 4))
            assertEquals("fmt ", ascii(bytes, 12, 4))
            assertEquals(16, intLe(bytes, 16))
            assertEquals(1, shortLe(bytes, 20)) // PCM
            assertEquals(1, shortLe(bytes, 22)) // mono
            assertEquals(16_000, intLe(bytes, 24))
            assertEquals(32_000, intLe(bytes, 28)) // byte rate
            assertEquals(2, shortLe(bytes, 32)) // block align
            assertEquals(16, shortLe(bytes, 34))
            assertEquals("data", ascii(bytes, 36, 4))
            assertEquals(samples * 2, intLe(bytes, 40))
        } finally {
            file.delete()
        }
    }

    @Test
    fun emptyRecordingStillHasPlayableHeader() {
        val file = File.createTempFile("winnie-wav", ".wav")
        try {
            WavFileWriter(file, 16_000).use { }
            val bytes = file.readBytes()
            assertEquals(WavFileWriter.HEADER_BYTES, bytes.size)
            assertEquals(36, intLe(bytes, 4))
            assertEquals(0, intLe(bytes, 40))
        } finally {
            file.delete()
        }
    }

    private fun ascii(bytes: ByteArray, offset: Int, length: Int) =
        String(bytes, offset, length, Charsets.US_ASCII)

    private fun intLe(bytes: ByteArray, offset: Int): Int =
        (bytes[offset].toInt() and 0xFF) or
            ((bytes[offset + 1].toInt() and 0xFF) shl 8) or
            ((bytes[offset + 2].toInt() and 0xFF) shl 16) or
            ((bytes[offset + 3].toInt() and 0xFF) shl 24)

    private fun shortLe(bytes: ByteArray, offset: Int): Int =
        (bytes[offset].toInt() and 0xFF) or
            ((bytes[offset + 1].toInt() and 0xFF) shl 8)
}
