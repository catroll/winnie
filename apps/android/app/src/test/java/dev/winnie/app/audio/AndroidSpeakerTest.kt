package dev.winnie.app.audio

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import dev.winnie.app.data.AppSettings
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * The engine itself needs a device: Robolectric can construct `TextToSpeech`
 * but cannot bind to a synthesiser service, so the successful path is not
 * reproducible here. What is worth proving is the failure path, because a phone
 * with no engine installed must say so instead of looking silent-but-fine.
 */
@RunWith(RobolectricTestRunner::class)
class AndroidSpeakerTest {

    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun anEngineThatNeverAnswersIsReportedAsUnavailable() = runBlocking {
        val speaker = AndroidSpeaker(context, AppSettings(context))
        try {
            val failure = withTimeout(15_000) {
                speaker.speak("今天天气怎么样？")
                speaker.failures.first()
            }
            assertEquals("This phone cannot read that reply aloud: no speech engine is installed for its language.", failure)
            assertEquals(false, speaker.speaking.value)
        } finally {
            speaker.release()
        }
    }

    @Test
    fun aStoppedSpeakerIsNotSpeaking() = runBlocking {
        val speaker = AndroidSpeaker(context, AppSettings(context))
        try {
            speaker.speak("Hello")
            speaker.stop()
            assertEquals(false, speaker.speaking.value)
        } finally {
            speaker.release()
        }
    }
}
