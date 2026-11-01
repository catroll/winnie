package dev.winnie.app

import android.content.Context
import dev.winnie.app.audio.AudioRecorder
import dev.winnie.app.audio.WakePrefs
import dev.winnie.app.audio.WavAudioRecorder
import dev.winnie.app.data.AppSettings
import dev.winnie.app.data.CredentialStore
import dev.winnie.app.data.KeystoreSecretBox
import dev.winnie.app.data.SessionManager
import dev.winnie.app.data.TokenStore
import dev.winnie.app.data.WinnieApi
import dev.winnie.app.phone.PhoneChangeDetector
import dev.winnie.app.phone.PhonePrefs
import dev.winnie.app.phone.PhoneSyncEngine
import dev.winnie.app.push.PushPrefs
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Manual dependency container. The app has one graph and no annotation
 * processor, so construction lives here instead of behind a DI framework.
 */
class AppContainer(context: Context) {

    private val appContext = context.applicationContext

    val settings = AppSettings(appContext)

    // One Keystore box for every saved secret, so the token and the remembered
    // credentials share one key rather than creating a second Keystore alias.
    private val secretBox = KeystoreSecretBox()
    val tokenStore = TokenStore(appContext, secretBox)
    val credentials = CredentialStore(appContext, secretBox)
    val sessions = SessionManager(tokenStore, settings, credentials)
    val phonePrefs = PhonePrefs(appContext)
    val pushPrefs = PushPrefs(appContext)
    val wakePrefs = WakePrefs(appContext)

    private val _voiceRequests = MutableStateFlow<VoiceTurnRequest?>(null)

    /**
     * A wake word was heard and the owner asked for a voice turn.
     *
     * The service and the activity are not guaranteed to be in one process
     * lifetime, so the hand-over is an intent extra that lands here; the signed-in
     * shell consumes it. [VoiceTurnRequest.id] makes repeated wakes distinct:
     * without it two identical requests would be equal and the second would be
     * dropped by the state flow.
     */
    val voiceRequests: StateFlow<VoiceTurnRequest?> = _voiceRequests.asStateFlow()

    private var voiceRequestId = 0L

    fun requestVoiceTurn(preRoll: ByteArray) {
        voiceRequestId += 1
        _voiceRequests.value = VoiceTurnRequest(voiceRequestId, preRoll)
    }

    fun consumeVoiceTurn() {
        _voiceRequests.value = null
    }

    /** The 5-second loop's local change check; sources are cheap to hold. */
    val phoneChangeDetector by lazy { PhoneChangeDetector(appContext, phonePrefs) }

    fun newRecorder(): AudioRecorder = WavAudioRecorder(appContext)

    /**
     * A client for background work. It reads the stored token instead of the
     * live session, because the worker runs without the UI process.
     */
    fun phoneApi(): WinnieApi? {
        val token = tokenStore.read() ?: return null
        return WinnieApi(settings.serverUrl, token)
    }

    fun newPhoneSyncEngine(): PhoneSyncEngine =
        PhoneSyncEngine(appContext, phonePrefs) { phoneApi() }
}

/** One pending hands-free turn, with the audio captured before the detection. */
data class VoiceTurnRequest(
    val id: Long,
    val preRoll: ByteArray,
)
