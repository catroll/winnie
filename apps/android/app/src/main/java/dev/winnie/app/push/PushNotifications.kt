package dev.winnie.app.push

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import dev.winnie.app.MainActivity
import dev.winnie.app.R
import dev.winnie.app.data.PushEvent

/**
 * Notification channels and the two kinds of notification the app posts.
 *
 * The foreground notification is the price of keeping the socket alive; it is
 * low importance and always present while the connection runs. Alerts are a
 * separate, high-importance channel so a user can silence the first without
 * losing reminders.
 */
object PushNotifications {

    const val BACKGROUND_CHANNEL = "winnie_background"
    const val ALERT_CHANNEL = "winnie_alerts"
    const val FOREGROUND_ID = 7101

    fun createChannels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
            ?: return
        val background = NotificationChannel(
            BACKGROUND_CHANNEL,
            "Background connection",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "The notification that keeps Winnie reachable."
            setShowBadge(false)
        }
        val alerts = NotificationChannel(
            ALERT_CHANNEL,
            "Winnie alerts",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "Reminders and results from Winnie."
        }
        manager.createNotificationChannels(listOf(background, alerts))
    }

    fun foreground(context: Context): Notification {
        val open = openApp(context, 0)
        val stop = PendingIntent.getService(
            context,
            1,
            Intent(context, WinniePushService::class.java)
                .setAction(WinniePushService.ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(context, BACKGROUND_CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle("Winnie is listening")
            .setContentText("Reminders arrive while this notification is shown.")
            .setContentIntent(open)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .addAction(0, "Stop", stop)
            .build()
    }

    fun show(context: Context, event: PushEvent) {
        if (!notificationsAllowed(context)) return
        val title = event.title.ifBlank { "Winnie" }
        val open = openApp(context, event.seq.toInt())
        val notification = NotificationCompat.Builder(context, ALERT_CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(title)
            .setContentText(event.body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(event.body))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .build()
        runCatching {
            NotificationManagerCompat.from(context)
                .notify(event.seq.toInt().coerceAtLeast(1), notification)
        }
    }

    fun notificationsAllowed(context: Context): Boolean =
        NotificationManagerCompat.from(context).areNotificationsEnabled()

    private fun openApp(context: Context, requestCode: Int): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        return PendingIntent.getActivity(
            context,
            requestCode,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }
}
