package dev.winnie.app.phone

import android.app.Notification
import android.content.pm.PackageManager
import android.os.Build
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import dev.winnie.app.data.PhoneNotificationItem
import dev.winnie.app.phone.sources.NotificationBuffer
import java.time.Instant
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * Captures notifications for the sync queue.
 *
 * Reading notifications needs the special "notification access" grant, which
 * the user gives in system settings; there is no runtime permission. Ongoing
 * notifications (music, navigation) are skipped because they update
 * continuously and would dominate the queue. Everything else is stored with a
 * client-generated id and uploaded on the next sync; nothing is sent from here
 * directly, so a dead network never blocks the system callback.
 */
class WinnieNotificationListener : NotificationListenerService() {

    private lateinit var prefs: PhonePrefs
    private lateinit var buffer: NotificationBuffer
    private val labels = ConcurrentHashMap<String, String>()

    override fun onCreate() {
        super.onCreate()
        prefs = PhonePrefs(this)
        buffer = NotificationBuffer(this)
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        if (sbn == null) return
        if (sbn.packageName == packageName) return
        if (!prefs.masterEnabled || !prefs.isEnabled(PhoneScope.NOTIFICATIONS)) return
        if (sbn.isOngoing) return

        val extras = sbn.notification.extras ?: return
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val text = (
            extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
                ?: extras.getCharSequence(Notification.EXTRA_TEXT)
            )?.toString().orEmpty()
        if (title.isBlank() && text.isBlank()) return

        val item = PhoneNotificationItem(
            externalId = UUID.randomUUID().toString(),
            packageName = sbn.packageName,
            appLabel = label(sbn.packageName),
            title = title.take(1000),
            text = text.take(4000),
            category = sbn.notification.category.orEmpty(),
            postedAt = Instant.ofEpochMilli(sbn.postTime).toString(),
            isOngoing = false,
            isClearable = sbn.isClearable,
        )
        runCatching { buffer.append(item) }
        PhoneSyncScheduler.requestSync(this, delaySeconds = 5)
    }

    private fun label(packageName: String): String = labels.getOrPut(packageName) {
        try {
            val manager = packageManager
            val info = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                manager.getApplicationInfo(
                    packageName,
                    PackageManager.ApplicationInfoFlags.of(0),
                )
            } else {
                @Suppress("DEPRECATION")
                manager.getApplicationInfo(packageName, 0)
            }
            manager.getApplicationLabel(info).toString()
        } catch (_: Exception) {
            packageName
        }
    }
}
