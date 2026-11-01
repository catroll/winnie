package dev.winnie.app.data

import dev.winnie.app.AppStrings
import dev.winnie.app.R
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/** Turns transport and API failures into a sentence a user can act on. */
fun Throwable.friendlyMessage(): String = when (this) {
    is UnauthorizedException -> message?.takeIf { it.isNotBlank() }
        ?: AppStrings.get(R.string.error_invalid_credentials)

    is ApiException -> when (statusCode) {
        413 -> AppStrings.get(R.string.error_recording_too_large)
        429 -> AppStrings.get(R.string.error_too_many_attempts)
        else -> message?.takeIf { it.isNotBlank() }
            ?: AppStrings.get(R.string.error_http_status, statusCode)
    }

    is UnknownHostException -> AppStrings.get(R.string.error_server_not_found)
    is ConnectException -> AppStrings.get(R.string.error_could_not_connect)
    is SocketTimeoutException -> AppStrings.get(R.string.error_timeout)
    is IOException -> message?.takeIf { it.isNotBlank() }
        ?: AppStrings.get(R.string.error_network)
    else -> message?.takeIf { it.isNotBlank() } ?: javaClass.simpleName
}
