package dev.winnie.app.push

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationManagerCompat
import dev.winnie.app.WinnieApplication
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * The two notification actions on a proactive reminder.
 *
 * An alert that carries an `item_id` can be acknowledged without opening the
 * app: the action hits `/api/v1/proactive/items/{id}/ack` with the stored
 * session, and the server stops the escalation ladder. Without this the phone
 * would only ever *receive* reminders; the ladder would keep climbing because
 * nothing on the phone could say "seen".
 *
 * `goAsync` is what keeps the process alive for the call after `onReceive`
 * returns. A failure cancels nothing: the notification stays, and the console
 * can still acknowledge it.
 */
class ProactiveAckReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val itemId = intent.getStringExtra(EXTRA_ITEM)?.trim().orEmpty()
        if (itemId.isEmpty()) return
        val snooze = intent.getStringExtra(EXTRA_ACTION) == ACTION_SNOOZE
        val notificationId = intent.getIntExtra(EXTRA_NOTIFICATION_ID, 0)
        val pending = goAsync()
        val application = context.applicationContext as? WinnieApplication
        if (application == null) {
            pending.finish()
            return
        }
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val api = application.container.phoneApi()
                if (api != null) {
                    if (snooze) api.snoozeProactive(itemId) else api.ackProactive(itemId)
                    if (notificationId != 0) {
                        NotificationManagerCompat.from(context).cancel(notificationId)
                    }
                }
            } catch (_: Exception) {
                // The reminder stays on screen; the server still has the item.
            } finally {
                pending.finish()
            }
        }
    }

    companion object {
        const val ACTION_ACK = "dev.winnie.app.proactive.ACK"
        const val ACTION_SNOOZE = "dev.winnie.app.proactive.SNOOZE"
        const val EXTRA_ITEM = "item_id"
        const val EXTRA_ACTION = "action"
        const val EXTRA_NOTIFICATION_ID = "notification_id"

        fun intent(
            context: Context,
            itemId: String,
            action: String,
            notificationId: Int,
        ): PendingIntent {
            val intent = Intent(context, ProactiveAckReceiver::class.java).apply {
                putExtra(EXTRA_ITEM, itemId)
                putExtra(EXTRA_ACTION, action)
                putExtra(EXTRA_NOTIFICATION_ID, notificationId)
            }
            val requestCode = notificationId * 10 + if (action == ACTION_SNOOZE) 1 else 0
            return PendingIntent.getBroadcast(
                context,
                requestCode,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
        }
    }
}
