package dev.winnie.app.audio

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import dev.winnie.app.AppStrings
import dev.winnie.app.MainActivity
import dev.winnie.app.R

/**
 * The two notifications the wake service owns.
 *
 * The service notification is the price of a microphone foreground service on
 * Android: it is permanent while listening and says so, rather than hiding a
 * hot microphone behind a quiet channel. The detection notification is the
 * reliable way into the app when the OS refuses a background activity start; it
 * carries the same pre-roll audio as the direct launch.
 */
object WakeNotifications {

    const val SERVICE_CHANNEL = "wake_service"
    const val ALERT_CHANNEL = "wake_alert"
    const val SERVICE_ID = 4210
    const val ALERT_ID = 4211

    fun createChannels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(
            NotificationChannel(
                SERVICE_CHANNEL,
                AppStrings.get(R.string.wake_channel_service),
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = AppStrings.get(R.string.wake_channel_service_description)
                setShowBadge(false)
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(
                ALERT_CHANNEL,
                AppStrings.get(R.string.wake_channel_alert),
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = AppStrings.get(R.string.wake_channel_alert_description)
            },
        )
    }

    fun service(context: Context, keyword: String): Notification {
        val stop = PendingIntent.getService(
            context,
            0,
            Intent(context, WakeWordService::class.java).setAction(WakeWordService.ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val open = voiceIntent(context, ByteArray(0))
        return NotificationCompat.Builder(context, SERVICE_CHANNEL)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle(AppStrings.get(R.string.wake_service_title))
            .setContentText(AppStrings.get(R.string.wake_service_text, keyword))
            .setContentIntent(open)
            .setOngoing(true)
            .setSilent(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .addAction(0, AppStrings.get(R.string.wake_service_stop), stop)
            .build()
    }

    fun detected(context: Context, keyword: String, preRoll: ByteArray): Notification {
        val open = voiceIntent(context, preRoll)
        return NotificationCompat.Builder(context, ALERT_CHANNEL)
            .setSmallIcon(R.drawable.ic_mic)
            .setContentTitle(AppStrings.get(R.string.wake_alert_title))
            .setContentText(AppStrings.get(R.string.wake_alert_text, keyword))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .build()
    }

    /**
     * Opens the chat in voice mode. The pre-roll rides in the intent extras:
     * two seconds of 16 kHz mono is 64 KB, well inside the binder limit, and it
     * means the transcript starts at the wake word instead of after it.
     */
    fun voiceIntent(context: Context, preRoll: ByteArray): PendingIntent {
        val intent = Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            .putExtra(MainActivity.EXTRA_VOICE_TURN, true)
            .putExtra(MainActivity.EXTRA_VOICE_PREROLL, preRoll)
        return PendingIntent.getActivity(
            context,
            ALERT_ID,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }
}
