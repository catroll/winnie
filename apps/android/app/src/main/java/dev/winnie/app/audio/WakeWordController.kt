package dev.winnie.app.audio

import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.content.ContextCompat

/**
 * The one place that turns the wake setting into a running service.
 *
 * Kept separate from the service so the screens never construct an `Intent`
 * themselves: starting a microphone foreground service from the wrong place
 * throws `ForegroundServiceStartNotAllowedException`, and a caller that does not
 * catch it crashes the app while switching a toggle.
 */
object WakeWordController {

    private const val TAG = "WinnieWake"

    fun isEnabled(context: Context): Boolean = WakePrefs(context).enabled

    fun setEnabled(context: Context, enabled: Boolean) {
        val prefs = WakePrefs(context)
        prefs.enabled = enabled
        if (enabled) start(context) else stop(context)
    }

    /**
     * Starts listening if the owner enabled it. Safe to call from any visible
     * screen, which is where Android permits this service to start.
     */
    fun start(context: Context) {
        if (!WakePrefs(context).enabled) return
        val intent = Intent(context, WakeWordService::class.java)
        try {
            ContextCompat.startForegroundService(context, intent)
        } catch (e: Exception) {
            // Android 12+ may refuse a foreground service started from the
            // background. That is a policy answer, not a bug: leave the setting
            // on so the next visible start succeeds, and say so in the log.
            Log.w(TAG, "could not start wake word service", e)
        }
    }

    fun stop(context: Context) {
        context.stopService(Intent(context, WakeWordService::class.java))
    }

    /**
     * Gives the microphone back to the listening service after a voice turn.
     * Called from a visible screen, so a plain `startService` is allowed.
     */
    fun resume(context: Context) {
        if (!WakePrefs(context).enabled) return
        runCatching {
            context.startService(
                Intent(context, WakeWordService::class.java)
                    .setAction(WakeWordService.ACTION_RESUME),
            )
        }.onFailure { Log.w(TAG, "could not resume wake word service", it) }
    }
}
