package dev.winnie.app.audio

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.File
import java.io.IOException
import kotlin.math.max
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Records microphone audio to a WAV file the backend can transcribe. */
interface AudioRecorder {
    fun start()
    suspend fun stop(): File
    fun cancel()
}

/**
 * 16 kHz, mono, 16-bit PCM. This is the smallest format every ASR vendor
 * accepts and keeps a one-hour meeting around 115 MB on disk.
 *
 * The writer is closed by the pump coroutine, never by the caller, so a
 * cancellation and a final append can never touch the file concurrently.
 */
class WavAudioRecorder(context: Context) : AudioRecorder {

    private val appContext = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private var active: Active? = null

    override fun start() {
        check(active == null) { "a recording is already in progress" }

        val directory = File(appContext.cacheDir, RECORDINGS_DIR).apply { mkdirs() }
        val file = File(directory, "recording-${System.currentTimeMillis()}.wav")

        val minBuffer = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuffer <= 0) {
            throw IOException("This device cannot record 16 kHz mono audio")
        }
        val bufferSize = max(minBuffer * 2, MIN_BUFFER_SIZE)

        @SuppressLint("MissingPermission") // The screen requests RECORD_AUDIO first.
        val record = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufferSize,
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            throw IOException("The microphone is not available")
        }

        val writer = WavFileWriter(
            file = file,
            sampleRate = SAMPLE_RATE,
            channels = 1,
            bitsPerSample = 16,
        )

        try {
            record.startRecording()
        } catch (e: Exception) {
            record.release()
            writer.close()
            file.delete()
            throw e
        }
        if (record.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            record.release()
            writer.close()
            file.delete()
            throw IOException("The microphone could not start recording")
        }

        val state = Active(record, writer, file)
        state.pump = scope.launch {
            try {
                val buffer = ByteArray(bufferSize)
                while (isActive) {
                    val read = record.read(buffer, 0, buffer.size)
                    if (read > 0) {
                        writer.append(buffer, read)
                    } else if (read < 0) {
                        throw IOException("AudioRecord.read failed with $read")
                    }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                state.failure = e
            } finally {
                runCatching { writer.close() }
            }
        }
        active = state
    }

    override suspend fun stop(): File = withContext(Dispatchers.IO) {
        val current = active ?: throw IllegalStateException("no recording in progress")
        active = null
        // stop() unblocks a read() that is waiting for the next buffer; the
        // pump then closes the WAV header in its finally block.
        runCatching { current.record.stop() }
        current.pump.cancelAndJoin()
        current.record.release()

        current.failure?.let { failure ->
            throw IOException("The recording failed: ${failure.message}", failure)
        }
        if (current.file.length() <= WavFileWriter.HEADER_BYTES) {
            current.file.delete()
            throw IOException("The recording is too short")
        }
        current.file
    }

    override fun cancel() {
        val current = active ?: return
        active = null
        runCatching { current.record.stop() }
        // Cleanup is asynchronous so cancel() stays callable from onCleared and
        // from a click handler. The pump closes the writer, then the file goes.
        scope.launch {
            current.pump.cancelAndJoin()
            runCatching { current.record.release() }
            current.file.delete()
        }
    }

    private class Active(
        val record: AudioRecord,
        val writer: WavFileWriter,
        val file: File,
    ) {
        lateinit var pump: Job

        @Volatile
        var failure: Throwable? = null
    }

    companion object {
        const val SAMPLE_RATE = 16_000
        private const val MIN_BUFFER_SIZE = 8192
        private const val RECORDINGS_DIR = "recordings"
    }
}
