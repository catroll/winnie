package dev.winnie.app.push

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Restarts the background connection after a reboot or an app update when the
 * owner had it enabled. Android may refuse a foreground-service start from the
 * background; the failure is recorded and the service starts next time the app
 * is opened.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action
        if (action != Intent.ACTION_BOOT_COMPLETED &&
            action != Intent.ACTION_MY_PACKAGE_REPLACED
        ) {
            return
        }
        if (!PushPrefs(context).enabled) return
        PushController.start(context)
    }
}
