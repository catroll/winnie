package dev.winnie.app.audio

import kotlin.math.log10
import kotlin.math.max
import kotlin.math.sqrt

/**
 * Decides when a spoken turn has ended, from the audio alone.
 *
 * Android has no built-in endpointer that a third-party app may use for a
 * hands-free turn, and a fixed recording length is either too short or wastes
 * time. This keeps the rule deliberately simple: after speech has been seen,
 * trailing silence ends the turn; a turn that never contains speech ends on its
 * own so the recorder cannot run forever.
 *
 * The thresholds are in dBFS and the defaults are for a phone held at arm's
 * length. [accept] is fed 20 ms frames and returns true exactly once, when the
 * caller should stop recording.
 *
 * Pure Kotlin on purpose: the endpointing rules are unit tested without a
 * device, and the microphone code only supplies frames.
 */
class EndpointDetector(
    sampleRate: Int = 16_000,
    private val frameMs: Int = 20,
    /** RMS above this counts as speech; 20 dB below full scale is quiet room noise. */
    private val speechFloorDbfs: Double = -38.0,
    private val startFrames: Int = 3,
    private val endSilenceMs: Int = 1_200,
    private val maxSpeechMs: Int = 20_000,
    private val maxSilenceMs: Int = 6_000,
) {

    private val frameBytes = sampleRate / 1000 * frameMs * BYTES_PER_SAMPLE
    private val pending = ByteArray(frameBytes)
    private var pendingBytes = 0
    private var activeFrames = 0
    private var silenceFrames = 0
    private var elapsedMs = 0
    private var finished = false

    /** True once the first frames above the floor arrived. */
    var heardSpeech: Boolean = false
        private set

    /**
     * Feeds PCM16 little-endian bytes and returns true when the turn is over.
     * Bytes that do not fill a frame are kept for the next call.
     */
    fun accept(pcm: ByteArray, length: Int = pcm.size): Boolean {
        if (finished) return false
        require(length in 0..pcm.size) { "length out of range: $length" }
        var offset = 0
        while (offset < length) {
            val copy = minOf(length - offset, frameBytes - pendingBytes)
            System.arraycopy(pcm, offset, pending, pendingBytes, copy)
            pendingBytes += copy
            offset += copy
            if (pendingBytes == frameBytes) {
                pendingBytes = 0
                if (checkFrame(pending)) return true
            }
        }
        return false
    }

    private fun checkFrame(frame: ByteArray): Boolean {
        elapsedMs += frameMs
        val rms = rmsOf(frame)
        val level = if (rms <= 0.0) -120.0 else 20.0 * log10(rms)
        if (level >= speechFloorDbfs) {
            activeFrames += 1
            silenceFrames = 0
            if (activeFrames >= startFrames) heardSpeech = true
        } else {
            activeFrames = 0
            if (heardSpeech) silenceFrames += 1
        }
        val silenceMs = silenceFrames * frameMs
        val done = (heardSpeech && silenceMs >= endSilenceMs) ||
            elapsedMs >= maxSpeechMs ||
            (!heardSpeech && elapsedMs >= maxSilenceMs)
        if (done) finished = true
        return done
    }

    private fun rmsOf(frame: ByteArray): Double {
        var sum = 0.0
        var index = 0
        while (index + 1 < frame.size) {
            val sample = ((frame[index + 1].toInt() shl 8) or
                (frame[index].toInt() and 0xFF)).toShort().toInt()
            sum += sample.toDouble() * sample.toDouble()
            index += BYTES_PER_SAMPLE
        }
        val count = max(1, frame.size / BYTES_PER_SAMPLE)
        return sqrt(sum / count) / 32768.0
    }

    private companion object {
        const val BYTES_PER_SAMPLE = 2
    }
}
