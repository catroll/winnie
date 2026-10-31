package dev.winnie.app.phone

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import dev.winnie.app.WinnieApplication

/**
 * Runs one sync in the background.
 *
 * A signed-out device or a disabled master switch is a success, not a retry:
 * the work was not applicable. A transport failure retries with WorkManager's
 * backoff, and an expired session fails without retrying because no amount of
 * retrying will produce a new token.
 */
class PhoneSyncWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val application = applicationContext as? WinnieApplication
            ?: return Result.failure()
        val container = application.container
        val prefs = container.phonePrefs
        if (!prefs.masterEnabled || prefs.enabledScopes().isEmpty()) {
            return Result.success()
        }
        val outcome = container.newPhoneSyncEngine().syncNow()
        return when {
            outcome.skipped -> Result.success()
            outcome.unauthorized -> Result.failure()
            outcome.retry -> Result.retry()
            else -> Result.success()
        }
    }
}
