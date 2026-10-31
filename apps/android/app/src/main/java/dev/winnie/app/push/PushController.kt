package dev.winnie.app.push

import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat

/** Starts and stops the background connection; the service does the work. */
object PushController {

    fun start(context: Context) {
        val prefs = PushPrefs(context)
        prefs.enabled = true
        try {
            ContextCompat.startForegroundService(
                context,
                Intent(context, WinniePushService::class.java),
            )
        } catch (e: Exception) {
            // Android 12+ can refuse a foreground-service start from the
            // background. The service will start when the app is opened.
            prefs.lastError = e.message ?: "Could not start the background service."
            PushStatusBus.update {
                it.copy(state = "blocked", error = prefs.lastError)
            }
        }
    }

    fun stop(context: Context) {
        PushPrefs(context).enabled = false
        context.stopService(Intent(context, WinniePushService::class.java))
        PushStatusBus.update { PushConnection(state = "stopped") }
    }

    fun ensureStarted(context: Context) {
        if (PushPrefs(context).enabled) start(context)
    }
}
