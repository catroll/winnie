package dev.winnie.app.phone

import dev.winnie.app.data.ApiException
import dev.winnie.app.data.UnauthorizedException
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import kotlinx.coroutines.CancellationException
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Which failures end a sync run and which only end one scope.
 *
 * The engine used to rethrow every `IOException`, and `ApiException` extends
 * `IOException`, so one HTTP error from any kind aborted the run: the phone
 * uploaded `device`, the server rejected `calendar` with a 500, and calls,
 * SMS, contacts, apps, notifications and location were never attempted.
 * `ApiException` must therefore be classified before `IOException`.
 */
class PhoneSyncPolicyTest {

    @Test
    fun aTransportFailureStopsTheRun() {
        assertTrue(stopsRun(ConnectException("refused")))
        assertTrue(stopsRun(SocketTimeoutException("timeout")))
        assertTrue(stopsRun(IOException("socket closed")))
    }

    @Test
    fun anHttpErrorOnlyStopsItsOwnScope() {
        assertFalse(stopsRun(ApiException(400, "bad item")))
        assertFalse(stopsRun(ApiException(422, "timezone: too long")))
        assertFalse(stopsRun(ApiException(500, "Internal Server Error")))
        assertFalse(stopsRun(ApiException(503, "phone sync is disabled")))
    }

    @Test
    fun aRevokedDeviceStopsTheRunSoItCanReRegister() {
        assertTrue(stopsRun(ApiException(404, "device not found")))
    }

    @Test
    fun anExpiredSessionStopsTheRun() {
        assertTrue(stopsRun(UnauthorizedException("session expired")))
    }

    @Test
    fun aCancelledSyncStopsTheRun() {
        assertTrue(stopsRun(CancellationException("cancelled")))
    }

    @Test
    fun anOrdinaryProviderFailureOnlyStopsItsOwnScope() {
        assertFalse(stopsRun(SecurityException("READ_CONTACTS denied")))
        assertFalse(stopsRun(IllegalStateException("cursor closed")))
    }

    @Test
    fun anEmptySnapshotReadOnlyStopsItsOwnScope() {
        // A blank read is one scope's problem: the run must still deliver the
        // scopes that did read, or one blanked provider would stall the phone.
        assertFalse(stopsRun(EmptySnapshotRead("contact", 1215, "read 0 rows")))
    }

    @Test
    fun anEmptyReadAfterANonEmptyUploadIsRefused() {
        // 2026-11-01: TAS-AL00's ROM kept the grant and answered zero rows for
        // contacts, calendar, calls and SMS. A full snapshot is a delete list,
        // so sending that would have erased a working device copy.
        assertTrue(refusesEmptySnapshot(items = 0, previous = 601))
        assertTrue(refusesEmptySnapshot(items = 0, previous = 17))
    }

    @Test
    fun theGuardDoesNotBlockAFirstSyncOrARealDeletion() {
        // Never synced this kind: an empty snapshot is the truth, not a blank.
        assertFalse(refusesEmptySnapshot(items = 0, previous = 0))
        // Rows arrived: send them, whatever the previous count was.
        assertFalse(refusesEmptySnapshot(items = 5, previous = 0))
        // The owner wiped the server copy, which clears the counter, and the
        // phone really has nothing left: this is the deliberate path.
        assertFalse(refusesEmptySnapshot(items = 0, previous = 0))
    }
}
