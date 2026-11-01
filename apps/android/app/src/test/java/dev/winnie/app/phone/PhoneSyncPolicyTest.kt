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
}
