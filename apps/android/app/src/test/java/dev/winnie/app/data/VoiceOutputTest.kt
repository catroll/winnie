package dev.winnie.app.data

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * The voice choice is a device setting, so it is read back from the same
 * `SharedPreferences` the app uses. It also proves the structure: the saved
 * value is `{provider, voice, rate}`, not a bare voice name, because a browser
 * voice and a phone-engine voice are not comparable.
 */
@RunWith(RobolectricTestRunner::class)
class VoiceOutputTest {

    private val context: Context = ApplicationProvider.getApplicationContext()

    @Before
    fun clearSettings() {
        context.getSharedPreferences("winnie_settings", Context.MODE_PRIVATE)
            .edit()
            .clear()
            .commit()
    }

    @Test
    fun readingAloudIsOnByDefaultAtNormalSpeed() {
        val output = AppSettings(context).voiceOutput

        assertEquals(true, output.enabled)
        assertEquals(VoiceOutput.SYSTEM, output.provider)
        assertEquals("", output.voice)
        assertEquals(1f, output.rate)
    }

    @Test
    fun everyFieldSurvivesARoundTrip() {
        val settings = AppSettings(context)
        settings.voiceOutput = VoiceOutput(
            enabled = false,
            provider = VoiceOutput.SYSTEM,
            voice = "zh-cn-x-ccc-network",
            rate = 1.2f,
        )

        val restored = AppSettings(context).voiceOutput

        assertEquals(false, restored.enabled)
        assertEquals(VoiceOutput.SYSTEM, restored.provider)
        assertEquals("zh-cn-x-ccc-network", restored.voice)
        assertEquals(1.2f, restored.rate)
    }

    @Test
    fun aRateOutsideTheEngineRangeIsClampedOnWriteAndRead() {
        val settings = AppSettings(context)

        settings.voiceOutput = VoiceOutput(rate = 9f)
        assertEquals(VoiceOutput.MAX_RATE, AppSettings(context).voiceOutput.rate)

        // A value written by an older build must not reach the engine either.
        context.getSharedPreferences("winnie_settings", Context.MODE_PRIVATE)
            .edit()
            .putFloat("voice_output_rate", 0.01f)
            .commit()
        assertEquals(VoiceOutput.MIN_RATE, AppSettings(context).voiceOutput.rate)
    }

    @Test
    fun theOfferedRatesAreTheOnesTheConsoleOffers() {
        assertEquals(listOf(0.8f, 1f, 1.2f, 1.5f), VoiceOutput.RATES)
    }
}
