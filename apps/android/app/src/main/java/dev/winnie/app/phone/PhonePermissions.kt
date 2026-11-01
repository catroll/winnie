package dev.winnie.app.phone

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import dev.winnie.app.AppStrings
import dev.winnie.app.R

/** Runtime and special-access checks for phone scopes. */
object PhonePermissions {

    fun granted(context: Context, permission: String): Boolean =
        ContextCompat.checkSelfPermission(context, permission) ==
            PackageManager.PERMISSION_GRANTED

    fun missing(context: Context, scope: PhoneScope): List<String> =
        scope.runtimePermissions.filterNot { granted(context, it) }

    fun notificationAccess(context: Context): Boolean =
        NotificationManagerCompat.getEnabledListenerPackages(context)
            .contains(context.packageName)

    fun backgroundLocation(context: Context): Boolean {
        // The separate background grant only exists from Android 10. Before
        // that, a foreground grant also covers background fixes.
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            return granted(context, Manifest.permission.ACCESS_FINE_LOCATION)
        }
        return granted(context, Manifest.permission.ACCESS_BACKGROUND_LOCATION)
    }

    /** True when the scope may read now; special access is checked as well. */
    fun ready(context: Context, scope: PhoneScope): Boolean {
        if (missing(context, scope).isNotEmpty()) return false
        if (scope.needsNotificationAccess && !notificationAccess(context)) return false
        return true
    }

    /**
     * A short reason a scope cannot be used, or an empty string when it can.
     * Background location is reported separately: a sync works without it, but
     * the location trail does not.
     */
    fun blocker(context: Context, scope: PhoneScope): String = when {
        missing(context, scope).isNotEmpty() ->
            AppStrings.get(R.string.common_permission_needed)
        scope.needsNotificationAccess && !notificationAccess(context) ->
            AppStrings.get(R.string.common_notification_access_needed)
        else -> ""
    }
}
