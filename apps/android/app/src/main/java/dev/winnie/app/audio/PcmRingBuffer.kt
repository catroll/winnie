package dev.winnie.app.audio

import kotlin.math.max
import kotlin.math.min

/**
 * Keeps the most recent PCM bytes in a fixed-size buffer.
 *
 * The wake detector runs on a continuous microphone stream, but the message
 * that follows the wake word starts before the detector can react (the keyword
 * model needs roughly 320 ms of context, then the phone has to hand the
 * microphone to the recorder). Without a buffer the first words of every
 * utterance would be cut off, so the service keeps the last two seconds and the
 * turn recorder writes them in front of what it captured itself.
 *
 * Not thread safe: the detector appends and reads on its own worker thread.
 */
class PcmRingBuffer(capacityBytes: Int) {

    private val buffer = ByteArray(max(capacityBytes, 0))
    private var writeIndex = 0
    private var filled = 0

    val capacity: Int get() = buffer.size
    val size: Int get() = filled

    fun clear() {
        writeIndex = 0
        filled = 0
    }

    /** Appends [length] bytes of [bytes]; older bytes are overwritten first. */
    fun append(bytes: ByteArray, length: Int = bytes.size) {
        require(length in 0..bytes.size) { "length out of range: $length" }
        if (buffer.isEmpty() || length == 0) return
        var offset = 0
        var remaining = length
        while (remaining > 0) {
            val chunk = min(remaining, buffer.size - writeIndex)
            System.arraycopy(bytes, offset, buffer, writeIndex, chunk)
            writeIndex = (writeIndex + chunk) % buffer.size
            filled = min(filled + chunk, buffer.size)
            offset += chunk
            remaining -= chunk
        }
    }

    /** A copy of the buffered bytes, oldest first. */
    fun snapshot(): ByteArray {
        if (filled == 0) return ByteArray(0)
        val result = ByteArray(filled)
        if (filled < buffer.size) {
            System.arraycopy(buffer, 0, result, 0, filled)
            return result
        }
        val tail = buffer.size - writeIndex
        System.arraycopy(buffer, writeIndex, result, 0, tail)
        System.arraycopy(buffer, 0, result, tail, writeIndex)
        return result
    }
}
