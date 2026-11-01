package dev.winnie.app.phone.sources

import android.content.Context
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.os.Build
import dev.winnie.app.data.PhoneAppItem
import java.time.Instant

/**
 * Lists installed applications. `QUERY_ALL_PACKAGES` is declared in the
 * manifest; without it the package manager hides most third-party apps.
 */
class AppSource(private val context: Context) {

    fun readAll(): List<PhoneAppItem> {
        val manager = context.packageManager
        val packages = try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                manager.getInstalledPackages(PackageManager.PackageInfoFlags.of(0))
            } else {
                @Suppress("DEPRECATION")
                manager.getInstalledPackages(0)
            }
        } catch (_: Exception) {
            return emptyList()
        }

        return packages.mapNotNull { info ->
            val application = info.applicationInfo ?: return@mapNotNull null
            val label = try {
                manager.getApplicationLabel(application).toString()
            } catch (_: Exception) {
                info.packageName
            }
            PhoneAppItem(
                externalId = info.packageName,
                label = label,
                packageName = info.packageName,
                versionName = info.versionName.orEmpty(),
                versionCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    info.longVersionCode
                } else {
                    @Suppress("DEPRECATION")
                    info.versionCode.toLong()
                },
                isSystem = application.flags and ApplicationInfo.FLAG_SYSTEM != 0,
                isEnabled = application.enabled,
                installedAt = info.firstInstallTime.takeIf { it > 0L }
                    ?.let { Instant.ofEpochMilli(it).toString() },
                updatedAt = info.lastUpdateTime.takeIf { it > 0L }
                    ?.let { Instant.ofEpochMilli(it).toString() },
            )
        }.sortedBy { it.label.lowercase() }
    }

    /**
     * Count plus the newest update time. The package manager query is the
     * expensive part, which is why the 5-second loop only runs this check on the
     * apps cadence (or when the app list is explicitly refreshed).
     */
    fun fingerprint(): String? {
        val manager = context.packageManager
        val packages = try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                manager.getInstalledPackages(PackageManager.PackageInfoFlags.of(0))
            } else {
                @Suppress("DEPRECATION")
                manager.getInstalledPackages(0)
            }
        } catch (_: Exception) {
            return null
        }
        var newest = 0L
        packages.forEach { newest = maxOf(newest, it.lastUpdateTime) }
        return "count:${packages.size}:updated:$newest"
    }
}
