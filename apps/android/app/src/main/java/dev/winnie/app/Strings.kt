package dev.winnie.app

import android.content.Context
import androidx.annotation.PluralsRes
import androidx.annotation.StringRes

/**
 * String resources for the places that are not Composables: view models, the
 * sync engine, notification builders, service status text.
 *
 * Android resolves these against the application context, which follows the
 * system locale — the same locale `stringResource` uses inside a screen, so the
 * two paths agree. Callers do not pass a Context through every constructor just
 * to build a sentence.
 */
object AppStrings {

    @Volatile
    private var context: Context? = null

    fun init(context: Context) {
        this.context = context.applicationContext
    }

    fun get(@StringRes id: Int, vararg args: Any): String {
        val resources = requireContext().resources
        return if (args.isEmpty()) {
            resources.getString(id)
        } else {
            resources.getString(id, *args)
        }
    }

    fun quantity(@PluralsRes id: Int, count: Int, vararg args: Any): String {
        val resources = requireContext().resources
        return if (args.isEmpty()) {
            resources.getQuantityString(id, count, count)
        } else {
            resources.getQuantityString(id, count, *args)
        }
    }

    private fun requireContext(): Context =
        context ?: error("AppStrings.init was not called by the Application")
}
