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
 * Schedules the background syncs that outlive the app process.
 *
 * `configure` keeps one periodic worker at the server's interval (never below
 * Android's 15-minute minimum) and is idempotent: a full sync calls it, so a
 * changed server interval takes effect without a restart.
 *
 * `requestSync` is the one-shot path for an explicit action — a scope toggle or
 * enabling sync — that must land even if the process is about to die. The
 * routine 5-second work is [PhoneSyncLoop], which is started with the process;
 * the content observers below only mark a scope dirty and wake that loop, so the
 * process no longer enqueues a worker for every row of a calendar edit.
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
     * Watches the four databases the app copies. A change marks the scope dirty
     * and wakes the 5-second loop, which reads only that scope. It is a cheap
     * in-process signal, not a WorkManager job: enqueueing a worker per row was
     * both slower and more expensive than the loop that now exists.
     *
     * Registering does not need the read permission; the loop checks the enabled
     * scopes anyway, so a later scope change needs no re-registration.
     */
    fun registerObservers(context: Context) {
        if (registered) return
        val handler = Handler(Looper.getMainLooper())
        val resolver: ContentResolver = context.contentResolver

        fun observe(uri: Uri, scope: PhoneScope) {
            val observer = object : ContentObserver(handler) {
                override fun onChange(selfChange: Boolean, uri: Uri?) {
                    PhoneChangeFlags.mark(scope)
                    PhoneSyncLoop.wake()
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

        observe(Telephony.Sms.CONTENT_URI, PhoneScope.MESSAGES)
        observe(CallLog.Calls.CONTENT_URI, PhoneScope.CALLS)
        observe(CalendarContract.Events.CONTENT_URI, PhoneScope.CALENDAR)
        observe(ContactsContract.Contacts.CONTENT_URI, PhoneScope.CONTACTS)
        registered = true
    }

    private fun networkConstraints(): Constraints = Constraints.Builder()
        .setRequiredNetworkType(NetworkType.CONNECTED)
        .build()
}
