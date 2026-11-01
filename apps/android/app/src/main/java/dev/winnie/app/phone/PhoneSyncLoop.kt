package dev.winnie.app.phone

import android.content.Context
import dev.winnie.app.AppContainer
import dev.winnie.app.WinnieApplication
import dev.winnie.app.data.SessionState
import dev.winnie.app.data.WinnieApi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Syncs the phone's data every five seconds while the app process is alive.
 *
 * The tick is not an upload: it is a local check. Each enabled scope has a cheap
 * change token (a cursor probe, a digest, a time bucket), and only a scope whose
 * token differs from the last successful upload costs a network round trip —
 * then only that scope is sent, and cursor kinds send only the rows above their
 * cursor. That is what makes a five-second cadence affordable: an idle phone
 * reads a few primary keys and sends nothing.
 *
 * Lifecycle. `Application.onCreate` starts the loop, so it runs while the app is
 * open and for as long as the alert foreground service keeps the process alive.
 * Android cannot hold a normal process in the background, so the 15-minute
 * WorkManager worker remains the safety net when the process is killed; this is
 * documented as such rather than pretended away.
 *
 * Failure. A transport failure backs off exponentially to five minutes and
 * resets on the next success. A content observer, a push `sync` frame or a
 * manual refresh wakes the loop immediately.
 */
object PhoneSyncLoop {

    private const val MAX_BACKOFF_MS = 5 * 60_000L

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val wakeChannel = Channel<Unit>(Channel.CONFLATED)

    private var job: Job? = null
    private var container: AppContainer? = null
    private var appContext: Context? = null
    private var cachedApi: WinnieApi? = null
    private var backoffMs = PhoneCadence.TICK_MS

    @Volatile
    private var forceCommands = false

    /** Starts the loop once per process. Idempotent. */
    fun start(context: Context) {
        if (job?.isActive == true) return
        val application = context.applicationContext as? WinnieApplication ?: return
        container = application.container
        appContext = application
        job = scope.launch { runLoop() }
    }

    /**
     * Wakes the loop before its next tick. [forceCommands] is set by a push
     * `sync` frame: a confirmed write must be claimed even when nothing local
     * changed and the command cadence has not elapsed.
     */
    fun wake(forceCommands: Boolean = false) {
        if (forceCommands) this.forceCommands = true
        wakeChannel.trySend(Unit)
    }

    private suspend fun runLoop() {
        while (scope.isActive) {
            val wait = try {
                tick()
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                backoffMs = nextBackoff()
                backoffMs
            }
            withTimeoutOrNull(wait) { wakeChannel.receive() }
        }
    }

    private suspend fun tick(): Long {
        val c = container ?: return PhoneCadence.TICK_MS
        val context = appContext ?: return PhoneCadence.TICK_MS
        val prefs = c.phonePrefs
        if (!prefs.masterEnabled || prefs.enabledScopes().isEmpty()) {
            backoffMs = PhoneCadence.TICK_MS
            return PhoneCadence.TICK_MS
        }
        val api = api(c) ?: run {
            backoffMs = PhoneCadence.TICK_MS
            return PhoneCadence.TICK_MS
        }

        val now = System.currentTimeMillis()
        val detector = c.phoneChangeDetector
        val check = detector.check(now, PhoneChangeFlags.drain())
        val commandsDue = forceCommands ||
            now - prefs.lastCommandPollAt >= PhoneCadence.COMMAND_POLL_MS

        if (!check.hasChanges && !commandsDue) {
            detector.markChecked(check.unchanged, now)
            backoffMs = PhoneCadence.TICK_MS
            return PhoneCadence.TICK_MS
        }
        forceCommands = false

        val engine = PhoneSyncEngine(context, prefs) { api }
        val outcome = engine.syncScopes(
            only = check.changed.map { it.scope }.toSet(),
            pollCommands = commandsDue,
        )

        if (outcome.unauthorized) {
            cachedApi = null
            c.sessions.onUnauthorized()
            return PhoneCadence.TICK_MS
        }
        if (outcome.retry) {
            backoffMs = nextBackoff()
            return backoffMs
        }

        // Commit only what the server accepted. A failed scope keeps its old
        // fingerprint and is retried, but backing off keeps a permanent per-kind
        // error (a rejected calendar row) from hitting the server every tick.
        detector.commit(
            check.changed.filter { it.scope in outcome.uploadedScopes },
            check.checkedAt,
        )
        detector.markChecked(check.unchanged, check.checkedAt)
        if (outcome.failedScopes.isNotEmpty()) {
            backoffMs = nextBackoff()
            return backoffMs
        }
        backoffMs = PhoneCadence.TICK_MS
        return PhoneCadence.TICK_MS
    }

    /**
     * The live session's client when there is one, so the hot path does not
     * decrypt the saved token five times a minute; the disk token is the
     * fallback for a process started headless by the push service.
     */
    private fun api(c: AppContainer): WinnieApi? = when (c.sessions.state.value) {
        is SessionState.LoggedIn -> {
            cachedApi?.let { return it }
            c.sessions.apiOrNull()?.also { cachedApi = it }
        }

        // A headless process (started by the push service) has no UI to run
        // restore(), so fall back to the token on disk — once.
        SessionState.Restoring -> cachedApi ?: c.phoneApi()?.also { cachedApi = it }

        // Signed out: never keep using a client whose token was just revoked.
        is SessionState.LoggedOut -> {
            cachedApi = null
            null
        }
    }

    private fun nextBackoff(): Long =
        (backoffMs * 2).coerceAtMost(MAX_BACKOFF_MS)
}
