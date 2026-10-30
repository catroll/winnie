package dev.winnie.app.audio

import java.io.Closeable
import java.io.File
import java.io.RandomAccessFile

/**
 * Writes a PCM WAV file incrementally.
 *
 * The 44-byte header is written up front with zero sizes and patched in
 * [close], so a long recording never has to be buffered or copied. Only sizes
 * that fit in a signed 32-bit field are supported; the server caps uploads far
 * below that limit.
 */
class WavFileWriter(
    val file: File,
    private val sampleRate: Int,
    private val channels: Int = 1,
    private val bitsPerSample: Int = 16,
) : Closeable {

    private val out = RandomAccessFile(file, "rw")
    private var dataBytes = 0L
    private var closed = false

    val bytesWritten: Long get() = dataBytes

    init {
        require(sampleRate > 0) { "sampleRate must be positive" }
        require(channels in 1..8) { "unsupported channel count: $channels" }
        require(bitsPerSample == 8 || bitsPerSample == 16) {
            "unsupported sample size: $bitsPerSample"
        }
        out.setLength(0)
        writeHeader()
    }

    /** Appends raw PCM bytes; [length] defaults to the whole buffer. */
    fun append(buffer: ByteArray, length: Int = buffer.size) {
        check(!closed) { "writer is closed" }
        require(length in 0..buffer.size) { "length out of range: $length" }
        if (length == 0) return
        out.write(buffer, 0, length)
        dataBytes += length
    }

    override fun close() {
        if (closed) return
        closed = true
        try {
            out.seek(4)
            writeIntLe((HEADER_BYTES - 8 + dataBytes).toInt())
            out.seek(40)
            writeIntLe(dataBytes.toInt())
        } finally {
            out.close()
        }
    }

    private fun writeHeader() {
        out.write(ASCII("RIFF"))
        writeIntLe(HEADER_BYTES - 8) // patched in close(); RIFF chunk size
        out.write(ASCII("WAVE"))
        out.write(ASCII("fmt "))
        writeIntLe(16)
        writeShortLe(1) // PCM
        writeShortLe(channels)
        writeIntLe(sampleRate)
        writeIntLe(sampleRate * channels * bitsPerSample / 8)
        writeShortLe(channels * bitsPerSample / 8)
        writeShortLe(bitsPerSample)
        out.write(ASCII("data"))
        writeIntLe(0) // patched in close()
    }

    private fun writeIntLe(value: Int) {
        out.write(value and 0xFF)
        out.write((value ushr 8) and 0xFF)
        out.write((value ushr 16) and 0xFF)
        out.write((value ushr 24) and 0xFF)
    }

    private fun writeShortLe(value: Int) {
        out.write(value and 0xFF)
        out.write((value ushr 8) and 0xFF)
    }

    private fun ASCII(value: String) = value.toByteArray(Charsets.US_ASCII)

    companion object {
        const val HEADER_BYTES = 44
    }
}
