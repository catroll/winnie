package dev.winnie.app.phone

import android.content.Context
import dev.winnie.app.phone.sources.AppSource
import dev.winnie.app.phone.sources.CalendarSource
import dev.winnie.app.phone.sources.CallLogSource
import dev.winnie.app.phone.sources.ContactsSource
import dev.winnie.app.phone.sources.NotificationBuffer
import dev.winnie.app.phone.sources.SmsSource

/** A scope whose change check found something to upload. */
data class ScopeChange(val scope: PhoneScope, val token: String)

/**
 * The result of one local check: what changed, what was checked and found
 * unchanged, and when. Only [changed] may cause a network round trip.
 */
data class ChangeCheck(
    val changed: List<ScopeChange> = emptyList(),
    val unchanged: List<PhoneScope> = emptyList(),
    val checkedAt: Long = 0L,
) {
    val hasChanges: Boolean get() = changed.isNotEmpty()
}

/**
 * Decides which scopes need an upload without talking to the server.
 *
 * Each scope has a cheap token: a cursor comparison for the append-only kinds, a
 * digest for a snapshot, a time bucket for the kinds that are sampled rather
 * than watched (location, device). A scope is only read when its cadence is due
 * or its observer fired, and it is only uploaded when the token differs from
 * the token stored after its last successful upload.
 *
 * The rules live in [decideChanges] so they can be tested without a provider.
 */
class PhoneChangeDetector(
    context: Context,
    private val prefs: PhonePrefs,
) {
    private val calls = CallLogSource(context)
    private val sms = SmsSource(context)
    private val calendar = CalendarSource(context)
    private val contacts = ContactsSource(context)
    private val apps = AppSource(context)
    private val notifications = NotificationBuffer(context)

    fun check(now: Long, dirty: Set<PhoneScope>): ChangeCheck = decideChanges(
        scopes = prefs.enabledScopes(),
        dirty = dirty,
        now = now,
        lastCheckAt = { prefs.lastCheckAt(it.key) },
        fingerprint = { prefs.fingerprint(it.key) },
        tokenFor = { scope -> tokenFor(scope, now) },
    )

    /**
     * Records the fingerprint of scopes whose upload succeeded. A failed scope
     * is left alone, so the next tick tries it again.
     */
    fun commit(changes: List<ScopeChange>, checkedAt: Long) {
        changes.forEach { change ->
            prefs.setFingerprint(change.scope.key, settledToken(change.scope, change.token))
            prefs.setLastCheckAt(change.scope.key, checkedAt)
        }
    }

    /**
     * Records the fingerprints of scopes a full sync just uploaded, so the next
     * loop tick starts from what the server already has instead of uploading
     * the same snapshot again. A scope that failed keeps its old fingerprint
     * and is retried.
     */
    fun commitCurrent(
        scopes: Iterable<PhoneScope>,
        now: Long,
        failed: Set<PhoneScope> = emptySet(),
    ) {
        val succeeded = scopes.filter { it !in failed }
        val changes = succeeded.mapNotNull { scope ->
            tokenFor(scope, now)?.let { ScopeChange(scope, it) }
        }
        commit(changes, now)
        markChecked(succeeded, now)
    }

    /** Records that a due scope was read and found unchanged. */
    fun markChecked(scopes: Iterable<PhoneScope>, checkedAt: Long) {
        scopes.forEach { prefs.setLastCheckAt(it.key, checkedAt) }
    }

    /** Forces the next check of [scope] to compare from scratch. */
    fun forget(scope: PhoneScope) {
        prefs.setFingerprint(scope.key, "")
        prefs.setLastCheckAt(scope.key, 0L)
    }

    private fun tokenFor(scope: PhoneScope, now: Long): String? = when (scope) {
        PhoneScope.CALLS -> if (calls.hasNew(cursorOf("call"))) "new" else "idle"
        PhoneScope.MESSAGES -> if (sms.hasNew(cursorOf("message"))) "new" else "idle"
        PhoneScope.NOTIFICATIONS -> if (notifications.size() > 0) "pending" else "idle"
        PhoneScope.CALENDAR -> calendar.fingerprint(
            PhoneSyncWindow.from(now),
            PhoneSyncWindow.to(now),
        )
        PhoneScope.CONTACTS -> contacts.fingerprint()
        PhoneScope.APPS -> apps.fingerprint()
        PhoneScope.LOCATION -> "location:${now / PhoneCadence.LOCATION_MS}"
        PhoneScope.DEVICE -> "device:${now / PhoneCadence.DEVICE_MS}"
    }

    private fun cursorOf(kind: String): Long = prefs.cursor(kind).toLongOrNull() ?: 0L

    /**
     * A successful upload leaves a cursor kind with nothing pending, so its
     * fingerprint settles to `idle` rather than the token that triggered it.
     */
    private fun settledToken(scope: PhoneScope, token: String): String = when (scope) {
        PhoneScope.CALLS, PhoneScope.MESSAGES, PhoneScope.NOTIFICATIONS -> "idle"
        else -> token
    }
}

/**
 * The pure rule set: a scope is examined when its observer fired or its cadence
 * elapsed; it is "changed" when the token differs from the stored fingerprint.
 * A token of null means the provider could not be read (a revoked permission,
 * for example) and the scope is left for the next check.
 */
internal fun decideChanges(
    scopes: Iterable<PhoneScope>,
    dirty: Set<PhoneScope>,
    now: Long,
    lastCheckAt: (PhoneScope) -> Long,
    fingerprint: (PhoneScope) -> String,
    tokenFor: (PhoneScope) -> String?,
): ChangeCheck {
    val changed = mutableListOf<ScopeChange>()
    val unchanged = mutableListOf<PhoneScope>()
    for (scope in scopes) {
        val due = scope in dirty ||
            now - lastCheckAt(scope) >= PhoneCadence.intervalMs(scope)
        if (!due) continue
        val token = tokenFor(scope) ?: continue
        if (token == fingerprint(scope)) {
            unchanged += scope
        } else {
            changed += ScopeChange(scope, token)
        }
    }
    return ChangeCheck(changed = changed, unchanged = unchanged, checkedAt = now)
}
