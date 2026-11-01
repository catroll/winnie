package dev.winnie.app.phone.sources

import android.accounts.AccountManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.os.StatFs
import android.provider.Settings
import android.telephony.TelephonyManager
import java.net.Inet4Address
import java.net.NetworkInterface
import java.util.Locale
import java.util.TimeZone
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import dev.winnie.app.AppStrings
import dev.winnie.app.BuildConfig
import dev.winnie.app.R
import dev.winnie.app.data.PhoneDevicePayload
import dev.winnie.app.data.PhoneDeviceSnapshot
import dev.winnie.app.phone.PhonePermissions
import dev.winnie.app.phone.PhoneScope
import dev.winnie.app.phone.ScopeReadHealth

/**
 * The device profile: identity, OS, battery, storage, network, SIM and accounts.
 * Everything here is optional context for the assistant, never a required row,
 * so each probe is individually guarded: a vendor ROM that refuses one call
 * must not fail the sync, and a missing runtime permission only empties one key.
 *
 * The profile also carries what answers "which phone, on what network, where":
 * the owner-set device name, the phone number, the carrier, the WiFi SSID and
 * the addresses of the interfaces that are up. Reverse geocoding of a location
 * fix is deliberately not done here — the server resolves it, so the phone never
 * sends its position to a third party and never repeats the same lookup.
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

    /**
     * The name the owner gave the phone in Settings, when the ROM exposes it;
     * otherwise the build model. None of this needs a permission.
     */
    private fun deviceName(): String {
        val configured = try {
            Settings.Global.getString(context.contentResolver, Settings.Global.DEVICE_NAME)
        } catch (_: Exception) {
            null
        }
        val model = Build.MODEL.orEmpty()
        return configured?.takeIf { it.isNotBlank() } ?: model.ifBlank {
            AppStrings.get(R.string.device_fallback_name)
        }
    }

    private fun info(): JsonObject = buildJsonObject {
        put("sdk_int", Build.VERSION.SDK_INT)
        put("device", Build.DEVICE.orEmpty())
        put("product", Build.PRODUCT.orEmpty())
        put("brand", Build.BRAND.orEmpty())
        put("board", Build.BOARD.orEmpty())
        put("hardware", Build.HARDWARE.orEmpty())
        put("fingerprint", Build.FINGERPRINT.orEmpty())
        put("security_patch", Build.VERSION.SECURITY_PATCH.orEmpty())
        put("abis", Build.SUPPORTED_ABIS.joinToString(","))
        put("locale", Locale.getDefault().toLanguageTag())
        androidId()?.let { put("android_id", it) }
        battery()?.let { (percent, charging) ->
            put("battery_percent", percent)
            put("battery_charging", charging)
        }
        storage()?.let { (free, total) ->
            put("storage_free_bytes", free)
            put("storage_total_bytes", total)
        }
        put("network", network())
        telephony()?.let { details ->
            details.operator?.let { put("sim_operator", it) }
            details.phoneType?.let { put("phone_type", it) }
            details.carrier?.let { put("carrier", it) }
            details.networkType?.let { put("network_type", it) }
            details.simState?.let { put("sim_state", it) }
            details.simCountry?.let { put("sim_country", it) }
            details.phoneNumber?.let { put("phone_number", it) }
        }
        wifi()?.let { details ->
            details.ssid?.let { put("wifi_ssid", it) }
            details.bssid?.let { put("wifi_bssid", it) }
            details.linkSpeed?.let { put("wifi_link_speed_mbps", it) }
            details.frequency?.let { put("wifi_frequency_mhz", it) }
        }
        interfaces().let { addresses ->
            putJsonArray("ipv4") { addresses.ipv4.forEach { add(it) } }
            putJsonArray("ipv6") { addresses.ipv6.forEach { add(it) } }
            addresses.wifiIp?.let { put("wifi_ip", it) }
            addresses.mobileIp?.let { put("mobile_ip", it) }
        }
        screen()?.let { (width, height) ->
            put("screen_width", width)
            put("screen_height", height)
        }
        putJsonArray("accounts") {
            accounts().forEach { add(it) }
        }
        // Whether the four provider scopes can be read at all. A vendor ROM may
        // keep the grant and answer with blank rows; without this the server
        // cannot tell that apart from an empty phone. See ScopeReadHealth.
        put("scope_read", ScopeReadHealth(context).probe())
    }

    private fun androidId(): String? = try {
        Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID)
            ?.takeIf { it.isNotBlank() }
    } catch (_: Exception) {
        null
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

    private data class TelephonyDetails(
        val operator: String?,
        val phoneType: String?,
        val carrier: String?,
        val networkType: String?,
        val simState: String?,
        val simCountry: String?,
        val phoneNumber: String?,
    )

    @Suppress("DEPRECATION")
    private fun telephony(): TelephonyDetails? = try {
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
            val simState = when (manager.simState) {
                TelephonyManager.SIM_STATE_READY -> "ready"
                TelephonyManager.SIM_STATE_ABSENT -> "absent"
                TelephonyManager.SIM_STATE_PIN_REQUIRED -> "pin_required"
                TelephonyManager.SIM_STATE_PUK_REQUIRED -> "puk_required"
                TelephonyManager.SIM_STATE_NETWORK_LOCKED -> "network_locked"
                TelephonyManager.SIM_STATE_UNKNOWN -> "unknown"
                else -> null
            }
            TelephonyDetails(
                operator = manager.simOperatorName.orEmpty().ifBlank { null },
                phoneType = phoneType,
                // The carrier serving the current network; it differs from the
                // SIM operator while roaming.
                carrier = manager.networkOperatorName.orEmpty().ifBlank { null },
                networkType = networkTypeName(manager.dataNetworkType),
                simState = simState,
                simCountry = manager.simCountryIso.orEmpty().ifBlank { null },
                phoneNumber = phoneNumber(manager),
            )
        }
    } catch (_: Exception) {
        null
    }

    /**
     * The SIM's own number. Many carriers leave it blank, and reading it needs
     * READ_PHONE_NUMBERS (READ_PHONE_STATE before Android 13); the device scope
     * requests both, and a refusal returns null instead of throwing.
     */
    @Suppress("DEPRECATION")
    private fun phoneNumber(manager: TelephonyManager): String? = try {
        manager.line1Number.orEmpty().takeIf { it.isNotBlank() }
    } catch (_: SecurityException) {
        null
    } catch (_: Exception) {
        null
    }

    private fun networkTypeName(value: Int): String? = when (value) {
        TelephonyManager.NETWORK_TYPE_GPRS,
        TelephonyManager.NETWORK_TYPE_EDGE,
        TelephonyManager.NETWORK_TYPE_CDMA,
        TelephonyManager.NETWORK_TYPE_1xRTT,
        TelephonyManager.NETWORK_TYPE_IDEN -> "2g"

        TelephonyManager.NETWORK_TYPE_UMTS,
        TelephonyManager.NETWORK_TYPE_EVDO_0,
        TelephonyManager.NETWORK_TYPE_EVDO_A,
        TelephonyManager.NETWORK_TYPE_HSDPA,
        TelephonyManager.NETWORK_TYPE_HSUPA,
        TelephonyManager.NETWORK_TYPE_HSPA,
        TelephonyManager.NETWORK_TYPE_EVDO_B,
        TelephonyManager.NETWORK_TYPE_EHRPD,
        TelephonyManager.NETWORK_TYPE_HSPAP -> "3g"

        TelephonyManager.NETWORK_TYPE_LTE -> "4g"

        20 -> "5g" // NETWORK_TYPE_NR; the constant itself is API 29

        else -> null
    }

    private data class WifiDetails(
        val ssid: String?,
        val bssid: String?,
        val linkSpeed: Int?,
        val frequency: Int?,
    )

    /**
     * SSID and BSSID need fine location; without it Android returns
     * "<unknown ssid>" and a placeholder MAC, which are dropped here rather
     * than stored as if they were data.
     */
    @Suppress("DEPRECATION")
    private fun wifi(): WifiDetails? = try {
        val manager = context.applicationContext.getSystemService(Context.WIFI_SERVICE)
            as? WifiManager
        val info = manager?.connectionInfo
        if (info == null || info.networkId == -1) {
            null
        } else {
            WifiDetails(
                ssid = info.ssid.orEmpty()
                    .trim('"')
                    .takeIf { it.isNotBlank() && !it.contains("unknown", ignoreCase = true) },
                bssid = info.bssid.orEmpty()
                    .takeIf { it.isNotBlank() && it != "02:00:00:00:00:00" },
                linkSpeed = info.linkSpeed.takeIf { it > 0 },
                frequency = info.frequency.takeIf { it > 0 },
            )
        }
    } catch (_: Exception) {
        null
    }

    private data class InterfaceAddresses(
        val ipv4: List<String>,
        val ipv6: List<String>,
        val wifiIp: String?,
        val mobileIp: String?,
    )

    /**
     * Addresses of the interfaces that are up, split by family. No permission is
     * needed, and it covers both the WiFi and the cellular address without the
     * deprecated ``WifiInfo.getIpAddress``.
     */
    private fun interfaces(): InterfaceAddresses {
        val ipv4 = mutableListOf<String>()
        val ipv6 = mutableListOf<String>()
        var wifiIp: String? = null
        var mobileIp: String? = null
        try {
            val enumeration = NetworkInterface.getNetworkInterfaces()
                ?: return InterfaceAddresses(emptyList(), emptyList(), null, null)
            while (enumeration.hasMoreElements()) {
                val nic = enumeration.nextElement()
                if (!nic.isUp || nic.isLoopback) continue
                val name = nic.name.orEmpty()
                for (address in nic.inetAddresses) {
                    val host = address.hostAddress ?: continue
                    if (host.contains('%')) continue // IPv6 scope id, not an address
                    if (address is Inet4Address) {
                        ipv4.add(host)
                        when {
                            name.startsWith("wlan") && wifiIp == null -> wifiIp = host
                            (name.startsWith("rmnet") || name.startsWith("ccmni")) &&
                                mobileIp == null -> mobileIp = host
                        }
                    } else {
                        ipv6.add(host)
                    }
                }
            }
        } catch (_: Exception) {
            return InterfaceAddresses(ipv4, ipv6, wifiIp, mobileIp)
        }
        return InterfaceAddresses(ipv4, ipv6, wifiIp, mobileIp)
    }

    private fun screen(): Pair<Int, Int>? = try {
        val metrics = context.resources.displayMetrics
        metrics.widthPixels to metrics.heightPixels
    } catch (_: Exception) {
        null
    }

    private fun accounts(): List<String> = try {
        if (!PhonePermissions.granted(context, android.Manifest.permission.GET_ACCOUNTS)) {
            emptyList()
        } else {
            AccountManager.get(context).accounts.map { "${it.name} (${it.type})" }
        }
    } catch (_: Exception) {
        emptyList()
    }

    companion object {
        fun scopeKeys(scopes: Set<PhoneScope>): List<String> =
            scopes.map { it.key }.sorted()
    }
}
