package dev.winnie.app.phone.sources

import android.accounts.AccountManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Build
import android.os.StatFs
import android.telephony.TelephonyManager
import dev.winnie.app.BuildConfig
import dev.winnie.app.AppStrings
import dev.winnie.app.R
import dev.winnie.app.data.PhoneDevicePayload
import dev.winnie.app.data.PhoneDeviceSnapshot
import dev.winnie.app.phone.PhoneScope
import java.util.Locale
import java.util.TimeZone
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray

/**
 * The device profile: identity, OS, battery, storage, network and accounts.
 * Everything here is optional context for the assistant, never a required row,
 * so each probe is individually guarded: a vendor ROM that refuses one call
 * must not fail the sync.
 */
class DeviceSource(private val context: Context) {

    fun snapshot(scopes: List<String>): PhoneDeviceSnapshot = PhoneDeviceSnapshot(
        name = deviceName(),
        manufacturer = Build.MANUFACTURER.orEmpty(),
        model = Build.MODEL.orEmpty(),
        osVersion = Build.VERSION.RELEASE.orEmpty(),
        appVersion = BuildConfig.VERSION_NAME,
        timezone = TimeZone.getDefault().id,
        scopes = scopes,
        info = info(),
    )

    fun registration(deviceKey: String, scopes: List<String>): PhoneDevicePayload =
        PhoneDevicePayload(
            deviceKey = deviceKey,
            name = deviceName(),
            platform = "android",
            manufacturer = Build.MANUFACTURER.orEmpty(),
            model = Build.MODEL.orEmpty(),
            osVersion = Build.VERSION.RELEASE.orEmpty(),
            appVersion = BuildConfig.VERSION_NAME,
            timezone = TimeZone.getDefault().id,
            scopes = scopes,
            info = info(),
        )

    private fun deviceName(): String {
        val model = Build.MODEL.orEmpty()
        return model.ifBlank { AppStrings.get(R.string.device_fallback_name) }
    }

    private fun info(): JsonObject = buildJsonObject {
        put("sdk_int", Build.VERSION.SDK_INT)
        put("device", Build.DEVICE.orEmpty())
        put("product", Build.PRODUCT.orEmpty())
        put("locale", Locale.getDefault().toLanguageTag())
        battery()?.let { (percent, charging) ->
            put("battery_percent", percent)
            put("battery_charging", charging)
        }
        storage()?.let { (free, total) ->
            put("storage_free_bytes", free)
            put("storage_total_bytes", total)
        }
        put("network", network())
        telephony()?.let { (operator, phoneType) ->
            put("sim_operator", operator)
            put("phone_type", phoneType)
        }
        screen()?.let { (width, height) ->
            put("screen_width", width)
            put("screen_height", height)
        }
        putJsonArray("accounts") {
            accounts().forEach { add(it) }
        }
    }

    private fun battery(): Pair<Int, Boolean>? = try {
        val intent = context.registerReceiver(
            null,
            IntentFilter(Intent.ACTION_BATTERY_CHANGED),
        )
        val level = intent?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = intent?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        val status = intent?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        if (level >= 0 && scale > 0) {
            val percent = (level * 100) / scale
            val charging = status == BatteryManager.BATTERY_STATUS_CHARGING ||
                status == BatteryManager.BATTERY_STATUS_FULL
            percent to charging
        } else {
            null
        }
    } catch (_: Exception) {
        null
    }

    private fun storage(): Pair<Long, Long>? = try {
        val stat = StatFs(context.filesDir.absolutePath)
        stat.availableBytes to stat.totalBytes
    } catch (_: Exception) {
        null
    }

    private fun network(): String = try {
        val manager = context.getSystemService(Context.CONNECTIVITY_SERVICE)
            as? ConnectivityManager
        val network = manager?.activeNetwork
        val capabilities = network?.let { manager.getNetworkCapabilities(it) }
        when {
            capabilities == null -> "offline"
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
            capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN) -> "vpn"
            else -> "other"
        }
    } catch (_: Exception) {
        "unknown"
    }

    @Suppress("DEPRECATION")
    private fun telephony(): Pair<String, String>? = try {
        val manager = context.getSystemService(Context.TELEPHONY_SERVICE)
            as? TelephonyManager
        if (manager == null) {
            null
        } else {
            val phoneType = when (manager.phoneType) {
                TelephonyManager.PHONE_TYPE_GSM -> "gsm"
                TelephonyManager.PHONE_TYPE_CDMA -> "cdma"
                TelephonyManager.PHONE_TYPE_SIP -> "sip"
                else -> "unknown"
            }
            manager.simOperatorName.orEmpty() to phoneType
        }
    } catch (_: Exception) {
        null
    }

    private fun screen(): Pair<Int, Int>? = try {
        val metrics = context.resources.displayMetrics
        metrics.widthPixels to metrics.heightPixels
    } catch (_: Exception) {
        null
    }

    private fun accounts(): List<String> = try {
        AccountManager.get(context).accounts.map { "${it.name} (${it.type})" }
    } catch (_: Exception) {
        emptyList()
    }

    companion object {
        fun scopeKeys(scopes: Set<PhoneScope>): List<String> =
            scopes.map { it.key }.sorted()
    }
}