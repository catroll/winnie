package dev.winnie.app.phone

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The rule that makes a five-second cadence affordable: a scope is read only
 * when its cadence is due or an observer marked it, and it is uploaded only when
 * its cheap token differs from the token stored after the last upload.
 */
class PhoneChangeDetectorTest {

    private fun decide(
        now: Long,
        tokens: Map<PhoneScope, String?>,
        fingerprints: Map<PhoneScope, String> = emptyMap(),
        lastCheckAt: Map<PhoneScope, Long> = emptyMap(),
        dirty: Set<PhoneScope> = emptySet(),
    ): ChangeCheck = decideChanges(
        scopes = tokens.keys,
        dirty = dirty,
        now = now,
        lastCheckAt = { lastCheckAt[it] ?: 0L },
        fingerprint = { fingerprints[it] ?: "" },
        tokenFor = { tokens[it] },
    )

    @Test
    fun aScopeIsNotReadBeforeItsCadence() {
        val result = decide(
            now = 10_000,
            lastCheckAt = mapOf(PhoneScope.CALENDAR to 9_900),
            tokens = mapOf(PhoneScope.CALENDAR to "digest-1"),
        )

        assertTrue(result.changed.isEmpty())
        assertTrue(result.unchanged.isEmpty())
    }

    @Test
    fun aDueScopeWithAnUnchangedTokenIsNotUploaded() {
        val result = decide(
            now = 10_000,
            lastCheckAt = mapOf(PhoneScope.CALLS to 4_000),
            fingerprints = mapOf(PhoneScope.CALLS to "idle"),
            tokens = mapOf(PhoneScope.CALLS to "idle"),
        )

        assertEquals(listOf(PhoneScope.CALLS), result.unchanged)
        assertFalse(result.hasChanges)
    }

    @Test
    fun aDueScopeWithANewTokenIsAChange() {
        val result = decide(
            now = 10_000,
            lastCheckAt = mapOf(PhoneScope.MESSAGES to 4_000),
            fingerprints = mapOf(PhoneScope.MESSAGES to "idle"),
            tokens = mapOf(PhoneScope.MESSAGES to "new"),
        )

        assertEquals("new", result.changed.single().token)
        assertEquals(PhoneScope.MESSAGES, result.changed.single().scope)
    }

    @Test
    fun anObserverBypassesTheCadenceOfAnExpensiveScope() {
        val result = decide(
            now = 1_000,
            lastCheckAt = mapOf(PhoneScope.APPS to 999_000),
            fingerprints = mapOf(PhoneScope.APPS to "count:1:updated:1"),
            tokens = mapOf(PhoneScope.APPS to "count:2:updated:2"),
            dirty = setOf(PhoneScope.APPS),
        )

        assertEquals(PhoneScope.APPS, result.changed.single().scope)
    }

    @Test
    fun anUnreadableProviderIsLeftForTheNextCheck() {
        val result = decide(
            now = 10_000,
            lastCheckAt = mapOf(PhoneScope.CONTACTS to 0),
            tokens = mapOf(PhoneScope.CONTACTS to null),
        )

        assertTrue(result.changed.isEmpty())
        assertTrue(result.unchanged.isEmpty())
    }

    @Test
    fun everyEnabledScopeIsExaminedOnceInTheSameTick() {
        val result = decide(
            now = 10_000,
            fingerprints = mapOf(PhoneScope.CALLS to "idle"),
            tokens = mapOf(
                PhoneScope.MESSAGES to "new",
                PhoneScope.CALLS to "idle",
                PhoneScope.NOTIFICATIONS to "pending",
            ),
        )

        assertEquals(
            setOf(PhoneScope.MESSAGES, PhoneScope.NOTIFICATIONS),
            result.changed.map { it.scope }.toSet(),
        )
        assertEquals(
            setOf(PhoneScope.CALLS),
            result.unchanged.toSet(),
        )
    }

    @Test
    fun theTickIsFiveSecondsAndSnapshotsHaveTheirOwnCeilings() {
        assertEquals(5_000L, PhoneCadence.TICK_MS)
        assertEquals(5_000L, PhoneCadence.intervalMs(PhoneScope.MESSAGES))
        assertTrue(PhoneCadence.intervalMs(PhoneScope.APPS) >
            PhoneCadence.intervalMs(PhoneScope.CALENDAR))
    }
}
