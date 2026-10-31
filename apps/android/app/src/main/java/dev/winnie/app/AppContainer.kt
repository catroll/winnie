package dev.winnie.app

import android.content.Context
import dev.winnie.app.audio.AudioRecorder
import dev.winnie.app.audio.WavAudioRecorder
import dev.winnie.app.data.AppSettings
import dev.winnie.app.data.SessionManager
import dev.winnie.app.data.TokenStore
import dev.winnie.app.data.WinnieApi
import dev.winnie.app.phone.PhonePrefs
import dev.winnie.app.phone.PhoneSyncEngine

/**
 * Manual dependency container. The app has one graph and no annotation
 * processor, so construction lives here instead of behind a DI framework.
 */
class AppContainer(context: Context) {

    private val appContext = context.applicationContext

    val settings = AppSettings(appContext)
    val tokenStore = TokenStore(appContext)
    val sessions = SessionManager(tokenStore, settings)
    val phonePrefs = PhonePrefs(appContext)

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
