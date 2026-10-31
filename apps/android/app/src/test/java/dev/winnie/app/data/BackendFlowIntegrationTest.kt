package dev.winnie.app.data

import dev.winnie.app.audio.WavFileWriter
import java.io.File
import kotlin.math.PI
import kotlin.math.sin
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test

/**
 * End-to-end check of the client used by the Android app against a real Winnie
 * backend. It is skipped unless WINNIE_ANDROID_BASE_URL is set, so the normal
 * test run stays hermetic:
 *
 *   WINNIE_ANDROID_BASE_URL=http://[::1]:8000 ./gradlew test
 *
 * The backend must allow registration and use a provider that does not need
 * cloud credentials (the fake ASR/translator/summarizer are enough to prove
 * the transport, multipart upload and polling path).
 */
class BackendFlowIntegrationTest {

    private val baseUrl: String? = System.getenv("WINNIE_ANDROID_BASE_URL")

    @Before
    fun requireBackend() {
        assumeTrue(
            "WINNIE_ANDROID_BASE_URL is not set; skipping the live backend test",
            !baseUrl.isNullOrBlank(),
        )
    }

    @Test
    fun recordUploadTranscribeAndRead() = runBlocking {
        val root = baseUrl!!.trimEnd('/')
        val username = "android${System.currentTimeMillis() % 10_000_000}"
        val password = "android-test-password"

        val anonymous = WinnieApi(root)
        val token = anonymous.register(username, password)
        val api = WinnieApi(root, token.accessToken)

        val meeting = api.createMeeting("Android client flow test", "zh")
        val wav = writeTestWav(seconds = 1)
        try {
            api.uploadAudio(meeting.id, wav, "android-test.wav")
            api.transcribe(meeting.id)

            var current = api.meeting(meeting.id)
            var waited = 0L
            while (!current.isReady && !current.isFailed && waited < 60_000) {
                delay(500)
                waited += 500
                current = api.meeting(meeting.id)
            }
            assertEquals("failed: ${current.error}", "ready", current.status)

            val transcript = api.transcript(meeting.id)
            assertTrue("transcript is empty", transcript.segments.isNotEmpty())

            val minutes = api.minutes(meeting.id)
            assertTrue("minutes summary is empty", minutes.summary.isNotBlank())

            println(
                "android flow ok: meeting=${meeting.id} status=${current.status} " +
                    "segments=${transcript.segments.size} " +
                    "first=${transcript.segments.first().text}",
            )

            api.deleteMeeting(meeting.id)
        } finally {
            wav.delete()
        }
    }

    private fun writeTestWav(seconds: Int): File {
        val file = File.createTempFile("winnie-android", ".wav")
        WavFileWriter(file, sampleRate = 16_000, channels = 1, bitsPerSample = 16).use { writer ->
            val frameSamples = 160
            val buffer = ByteArray(frameSamples * 2)
            var written = 0
            val total = 16_000 * seconds
            while (written < total) {
                for (index in 0 until frameSamples) {
                    val position = written + index
                    val value = (sin(2 * PI * 440.0 * position / 16_000.0) * 12_000).toInt()
                    buffer[index * 2] = (value and 0xFF).toByte()
                    buffer[index * 2 + 1] = ((value shr 8) and 0xFF).toByte()
                }
                writer.append(buffer)
                written += frameSamples
            }
        }
        return file
    }
}
