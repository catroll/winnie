package dev.winnie.app.phone.sources

import android.content.Context
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.Bundle
import android.os.CancellationSignal
import android.os.Looper
import androidx.core.content.ContextCompat
import dev.winnie.app.data.PhoneLocationItem
import java.time.Instant
import java.util.UUID
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

/**
 * One location fix per sync, never a continuous foreground track.
 *
 * A recent last-known fix is reused; otherwise the app asks for a single
 * update with a short timeout. In the background this needs "allow all the
 * time" location access on Android 10+, so a refusal returns null instead of
 * failing the sync.
 */
class LocationSource(private val context: Context) {

    suspend fun capture(): PhoneLocationItem? = withContext(Dispatchers.IO) {
        val manager = context.getSystemService(Context.LOCATION_SERVICE)
            as? LocationManager ?: return@withContext null
        val last = lastKnown(manager)
        val fresh = if (last == null || System.currentTimeMillis() - last.time > FRESH_MS) {
            requestCurrent(manager)
        } else {
            null
        }
        val fix = fresh ?: last ?: return@withContext null
        PhoneLocationItem(
            externalId = UUID.randomUUID().toString(),
            latitude = fix.latitude,
            longitude = fix.longitude,
            accuracyM = fix.accuracy.takeIf { fix.hasAccuracy() }?.toDouble(),
            altitudeM = fix.altitude.takeIf { fix.hasAltitude() },
            speedMps = fix.speed.takeIf { fix.hasSpeed() }?.toDouble(),
            provider = fix.provider.orEmpty(),
            capturedAt = Instant.ofEpochMilli(fix.time).toString(),
        )
    }

    private fun lastKnown(manager: LocationManager): Location? {
        val providers = listOf(
            LocationManager.GPS_PROVIDER,
            LocationManager.NETWORK_PROVIDER,
            LocationManager.PASSIVE_PROVIDER,
        )
        return providers
            .mapNotNull { provider ->
                try {
                    manager.getLastKnownLocation(provider)
                } catch (_: SecurityException) {
                    null
                } catch (_: Exception) {
                    null
                }
            }
            .maxByOrNull { it.time }
    }

    private suspend fun requestCurrent(manager: LocationManager): Location? {
        val provider = listOf(
            LocationManager.GPS_PROVIDER,
            LocationManager.NETWORK_PROVIDER,
        ).firstOrNull {
            try {
                manager.isProviderEnabled(it)
            } catch (_: Exception) {
                false
            }
        } ?: return null

        return withTimeoutOrNull(TIMEOUT_MS) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                requestWithCancellationSignal(manager, provider)
            } else {
                requestWithSingleUpdate(manager, provider)
            }
        }
    }

    @androidx.annotation.RequiresApi(Build.VERSION_CODES.R)
    private suspend fun requestWithCancellationSignal(
        manager: LocationManager,
        provider: String,
    ): Location? = suspendCancellableCoroutine { continuation ->
        val signal = CancellationSignal()
        val executor = ContextCompat.getMainExecutor(context)
        try {
            manager.getCurrentLocation(provider, signal, executor) { location ->
                if (continuation.isActive) continuation.resume(location)
            }
        } catch (_: SecurityException) {
            if (continuation.isActive) continuation.resume(null)
        } catch (_: Exception) {
            if (continuation.isActive) continuation.resume(null)
        }
        continuation.invokeOnCancellation { signal.cancel() }
    }

    @Suppress("DEPRECATION")
    private suspend fun requestWithSingleUpdate(
        manager: LocationManager,
        provider: String,
    ): Location? = suspendCancellableCoroutine { continuation ->
        val listener = object : LocationListener {
            override fun onLocationChanged(location: Location) {
                if (continuation.isActive) continuation.resume(location)
            }

            override fun onProviderEnabled(provider: String) = Unit

            override fun onProviderDisabled(provider: String) = Unit

            @Deprecated("Deprecated in the platform; still part of the interface")
            override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) = Unit
        }
        try {
            manager.requestSingleUpdate(provider, listener, Looper.getMainLooper())
            continuation.invokeOnCancellation { manager.removeUpdates(listener) }
        } catch (_: SecurityException) {
            if (continuation.isActive) continuation.resume(null)
        } catch (_: Exception) {
            if (continuation.isActive) continuation.resume(null)
        }
    }

    private companion object {
        const val FRESH_MS = 5 * 60 * 1000L
        const val TIMEOUT_MS = 20_000L
    }
}
