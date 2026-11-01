package dev.winnie.app.phone

/**
 * In-process dirty marks for phone scopes.
 *
 * A content observer fires on a change, this object records it, and the
 * 5-second loop drains the marks on its next tick. The mark makes the check
 * immediate for a scope whose own cadence is slower than the tick (calendar,
 * contacts) — the provider's change notification is free, so there is no reason
 * to wait for the fingerprint interval.
 *
 * Marks do not survive process death. That is what the persisted fingerprints
 * are for: the first tick after a restart still compares against what was last
 * uploaded.
 */
object PhoneChangeFlags {

    private val dirty = mutableSetOf<String>()

    @Synchronized
    fun mark(scope: PhoneScope) {
        dirty += scope.key
    }

    @Synchronized
    fun drain(): Set<PhoneScope> {
        val result = dirty.mapNotNull { PhoneScope.fromKey(it) }.toSet()
        dirty.clear()
        return result
    }

    @Synchronized
    fun clear() {
        dirty.clear()
    }
}
