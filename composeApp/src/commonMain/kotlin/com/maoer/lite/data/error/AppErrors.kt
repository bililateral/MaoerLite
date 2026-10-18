package com.maoer.lite.data.error

import io.ktor.client.plugins.HttpRequestTimeoutException
import io.ktor.client.network.sockets.ConnectTimeoutException
import io.ktor.client.network.sockets.SocketTimeoutException
import io.ktor.utils.io.errors.IOException
import kotlinx.serialization.SerializationException

/** Messages have a single source: agent-server/maoer_agent/resources/error_catalog.json. */
fun errorText(code: String, vararg parameters: Pair<String, Any?>): String {
    val safeCode = code.takeIf { it.matches(Regex("[a-z][a-z0-9_]{0,79}")) } ?: "unknown"
    val values = mapOf("code" to safeCode) + parameters.toMap()
    val template = errorMessages[code] ?: errorMessages.getValue("unknown_error")
    return Regex("\\{([a-z_]+)\\}").replace(template) { match ->
        values[match.groupValues[1]]?.toString()
            ?: errorMessages.getValue("unknown_value")
    }
}

class AppFailure(val code: String, val details: Map<String, Any?> = emptyMap()) : Exception(code)

fun failureText(error: Throwable, fallback: String): String = if (error is AppFailure)
    errorText(error.code, *error.details.toList().toTypedArray()) else errorText(fallback)

fun serviceFailureCode(error: Throwable): String = when (error) {
    is AppFailure -> error.code
    is ConnectTimeoutException -> "service_connect_timeout"
    is SocketTimeoutException -> "service_read_timeout"
    is HttpRequestTimeoutException -> "service_request_timeout"
    is SerializationException -> "service_response_invalid"
    is IOException -> "service_connection_failed"
    else -> "local_operation_failed"
}

fun rssFailureCode(error: Throwable): String = when (error) {
    is AppFailure -> error.code
    is ConnectTimeoutException -> "rss_connect_timeout"
    is SocketTimeoutException -> "rss_read_timeout"
    is HttpRequestTimeoutException -> "rss_request_timeout"
    is IOException -> "rss_connection_failed"
    else -> "rss_load_failed"
}
