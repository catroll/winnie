package dev.winnie.app

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import dev.winnie.app.push.PushController
import dev.winnie.app.ui.WinnieRoot
import dev.winnie.app.ui.theme.WinnieTheme

class MainActivity : ComponentActivity() {

    private lateinit var container: AppContainer

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        container = (application as WinnieApplication).container
        // Opening the app is a user-visible action, so this is an allowed place
        // to restart the background connection if the owner had it on.
        PushController.ensureStarted(this)
        // A wake can arrive while the task is already open or while the process
        // is cold; both paths end here.
        handleVoiceIntent(intent)
        setContent {
            WinnieTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    WinnieRoot(container)
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleVoiceIntent(intent)
    }

    /**
     * Turns the wake service's intent into a pending voice turn.
     *
     * The extra is removed straight away: the activity can be recreated for a
     * configuration change, and replaying the pre-roll would start a second
     * recording of the same utterance.
     */
    private fun handleVoiceIntent(intent: Intent?) {
        if (intent?.getBooleanExtra(EXTRA_VOICE_TURN, false) != true) return
        val preRoll = intent.getByteArrayExtra(EXTRA_VOICE_PREROLL) ?: ByteArray(0)
        intent.removeExtra(EXTRA_VOICE_TURN)
        intent.removeExtra(EXTRA_VOICE_PREROLL)
        container.requestVoiceTurn(preRoll)
    }

    companion object {
        /** Set by [dev.winnie.app.audio.WakeWordService] when a wake is heard. */
        const val EXTRA_VOICE_TURN = "dev.winnie.app.extra.VOICE_TURN"

        /** Two seconds of 16 kHz mono PCM captured before the detection. */
        const val EXTRA_VOICE_PREROLL = "dev.winnie.app.extra.VOICE_PREROLL"
    }
}
