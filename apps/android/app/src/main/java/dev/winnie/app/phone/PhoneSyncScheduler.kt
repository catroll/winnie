package dev.winnie.app.phone

import android.content.ContentResolver
import android.content.Context
import android.database.ContentObserver
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.provider.CalendarContract
import android.provider.CallLog
import android.provider.ContactsContract
import android.provider.Telephony
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import java.util.concurrent.TimeUnit

/**
 * Schedules syncs.
 *
 * `configure` keeps one periodic worker at the server's interval (never below
 * Android's 15-minute minimum) and is idempotent: the sync engine calls it after
 * every run, so a changed server interval takes effect without a restart.
 * `requestSync` debounces a one-shot run when data changes or a notification
 * arrives; content observers only fire while the process is alive, which is why
 * the periodic worker is the real safety net.
 */
object PhoneSyncScheduler {

    private const val PERIODIC_WORK = "phone-sync-periodic"
    private const val ONE_SHOT_WORK = "phone-sync-one-shot"
    private const val MIN_INTERVAL_MINUTES = 15L

    private val observers = mutableListOf<ContentObserver>()
    private var registered = false

    fun configure(context: Context, intervalMinutes: Int = 30) {
        val interval = intervalMinutes.toLong().coerceAtLeast(MIN_INTERVAL_MINUTES)
        val request = PeriodicWorkRequestBuilder<PhoneSyncWorker>(
            interval,
            TimeUnit.MINUTES,
        )
            .setConstraints(networkConstraints())
            .setBackoffCriteria(
                BackoffPolicy.EXPONENTIAL,
                30,
                TimeUnit.SECONDS,
            )
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            PERIODIC_WORK,
            ExistingPeriodicWorkPolicy.UPDATE,
            request,
        )
    }

    fun requestSync(context: Context, delaySeconds: Long = 3) {
        val request = OneTimeWorkRequestBuilder<PhoneSyncWorker>()
            .setConstraints(networkConstraints())
            .setInitialDelay(delaySeconds.coerceAtLeast(0), TimeUnit.SECONDS)
            .setBackoffCriteria(
                BackoffPolicy.EXPONENTIAL,
                30,
                TimeUnit.SECONDS,
            )
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(
            ONE_SHOT_WORK,
            ExistingWorkPolicy.KEEP,
            request,
        )
    }

    fun cancel(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(PERIODIC_WORK)
        WorkManager.getInstance(context).cancelUniqueWork(ONE_SHOT_WORK)
    }

    /**
     * Watches the four databases the app copies. Registering does not need the
     * read permission, and the sync engine checks the enabled scopes anyway,
     * so a later scope change needs no re-registration.
     */
    fun registerObservers(context: Context) {
        if (registered) return
        val handler = Handler(Looper.getMainLooper())
        val trigger = Runnable { requestSync(context, 3) }
        val resolver: ContentResolver = context.contentResolver

        fun observe(uri: Uri) {
            val observer = object : ContentObserver(handler) {
                override fun onChange(selfChange: Boolean, uri: Uri?) {
                    handler.removeCallbacks(trigger)
                    handler.postDelayed(trigger, 3000)
                }
            }
            try {
                resolver.registerContentObserver(uri, true, observer)
                observers += observer
            } catch (_: Exception) {
                // A provider can be absent on a stripped-down ROM; the
                // periodic worker still covers the scope.
            }
        }

        observe(Telephony.Sms.CONTENT_URI)
        observe(CallLog.Calls.CONTENT_URI)
        observe(CalendarContract.Events.CONTENT_URI)
        observe(ContactsContract.Contacts.CONTENT_URI)
        registered = true
    }

    private fun networkConstraints(): Constraints = Constraints.Builder()
        .setRequiredNetworkType(NetworkType.CONNECTED)
        .build()
}
