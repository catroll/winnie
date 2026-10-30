package dev.winnie.app

import android.content.Context
import dev.winnie.app.audio.AudioRecorder
import dev.winnie.app.audio.WavAudioRecorder
import dev.winnie.app.data.AppSettings
import dev.winnie.app.data.SessionManager
import dev.winnie.app.data.TokenStore

/**
 * Manual dependency container. The app has one graph and no annotation
 * processor, so construction lives here instead of behind a DI framework.
 */
class AppContainer(context: Context) {

    private val appContext = context.applicationContext

    val settings = AppSettings(appContext)
    val tokenStore = TokenStore(appContext)
    val sessions = SessionManager(tokenStore, settings)

    fun newRecorder(): AudioRecorder = WavAudioRecorder(appContext)
}
