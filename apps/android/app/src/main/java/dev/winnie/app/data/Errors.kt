package dev.winnie.app.data

import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/** Turns transport and API failures into a sentence a user can act on. */
fun Throwable.friendlyMessage(): String = when (this) {
    is UnauthorizedException -> message?.takeIf { it.isNotBlank() }
        ?: "Invalid username or password."

    is ApiException -> when (statusCode) {
        413 -> "The recording is too large for the server."
        429 -> "Too many attempts. Try again in a few minutes."
        else -> message?.takeIf { it.isNotBlank() }
            ?: "The server returned HTTP $statusCode."
    }

    is UnknownHostException -> "Server not found. Check the address."
    is ConnectException -> "Could not connect to the server. Check the address and that Winnie is running."
    is SocketTimeoutException -> "The server took too long to respond."
    is IOException -> message?.takeIf { it.isNotBlank() } ?: "Network error."
    else -> message?.takeIf { it.isNotBlank() } ?: javaClass.simpleName
}
