package dev.winnie.app.audio

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EndpointDetectorTest {

    private val sampleRate = 16_000
    private val frameMs = 20

    /** 20 ms of PCM16 at [amplitude] (0..1 of full scale). */
    private fun frame(amplitude: Double): ByteArray {
        val samples = sampleRate / 1000 * frameMs
        val peak = (amplitude * 32_767).toInt()
        val bytes = ByteArray(samples * 2)
        for (i in 0 until samples) {
            // A square wave makes the RMS exactly `peak`, so the level is
            // predictable instead of depending on a sine's phase.
            val value = if (i % 2 == 0) peak else -peak
            bytes[i * 2] = (value and 0xFF).toByte()
            bytes[i * 2 + 1] = ((value shr 8) and 0xFF).toByte()
        }
        return bytes
    }

    private fun feed(detector: EndpointDetector, frames: Int, amplitude: Double): Int {
        var endedAt = -1
        repeat(frames) { index ->
            if (detector.accept(frame(amplitude))) {
                endedAt = index
                return endedAt
            }
        }
        return endedAt
    }

    @Test
    fun `silence alone ends the turn without claiming speech`() {
        val detector = EndpointDetector(sampleRate = sampleRate)
        val endedAt = feed(detector, frames = 400, amplitude = 0.001)
        // 6 s of silence at 20 ms per frame.
        assertTrue(endedAt in 298..302)
        assertFalse(detector.heardSpeech)
    }

    @Test
    fun `speech followed by silence ends the turn after the trailing pause`() {
        val detector = EndpointDetector(sampleRate = sampleRate)
        assertFalse(feed(detector, frames = 10, amplitude = 0.2) >= 0)
        assertTrue(detector.heardSpeech)
        val endedAt = feed(detector, frames = 100, amplitude = 0.001)
        // 1200 ms of silence at 20 ms per frame, counted from the first silent
        // frame after speech.
        assertTrue("endedAt=$endedAt", endedAt in 58..63)
    }

    @Test
    fun `a short burst does not count as speech`() {
        val detector = EndpointDetector(sampleRate = sampleRate)
        feed(detector, frames = 2, amplitude = 0.2)
        assertFalse(detector.heardSpeech)
    }

    @Test
    fun `continuous speech is cut at the maximum turn length`() {
        val detector = EndpointDetector(sampleRate = sampleRate)
        val endedAt = feed(detector, frames = 1200, amplitude = 0.2)
        assertTrue("endedAt=$endedAt", endedAt in 998..1002)
        assertTrue(detector.heardSpeech)
    }

    @Test
    fun `an empty feed never ends the turn`() {
        val detector = EndpointDetector(sampleRate = sampleRate)
        assertFalse(detector.accept(ByteArray(0)))
        assertFalse(detector.accept(frame(0.2), frame(0.2).size))
    }

    @Test
    fun `a partial frame is kept until the next call completes it`() {
        val detector = EndpointDetector(sampleRate = sampleRate)
        val half = frame(0.2).copyOfRange(0, 100)
        assertFalse(detector.accept(half))
        assertFalse(detector.heardSpeech)
        assertFalse(detector.accept(frame(0.2)))
    }
}
