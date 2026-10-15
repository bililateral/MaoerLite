package com.maoer.lite.data.manager

import androidx.media3.common.PlaybackException
import androidx.media3.datasource.HttpDataSource
import com.maoer.lite.data.error.errorText

internal fun playbackFailureMessage(error: PlaybackException): String {
    val http = generateSequence<Throwable>(error) { it.cause?.takeUnless { next -> next === it } }.take(8)
        .filterIsInstance<HttpDataSource.InvalidResponseCodeException>().firstOrNull()
    if (http != null) return errorText("playback_http_error", "status" to http.responseCode)
    val code = when (error.errorCode) {
        PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT -> "playback_network_timeout"
        PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED -> "playback_network_failed"
        PlaybackException.ERROR_CODE_IO_FILE_NOT_FOUND -> "playback_file_missing"
        PlaybackException.ERROR_CODE_IO_NO_PERMISSION -> "playback_permission"
        PlaybackException.ERROR_CODE_PARSING_CONTAINER_MALFORMED,
        PlaybackException.ERROR_CODE_PARSING_CONTAINER_UNSUPPORTED,
        PlaybackException.ERROR_CODE_PARSING_MANIFEST_MALFORMED,
        PlaybackException.ERROR_CODE_PARSING_MANIFEST_UNSUPPORTED,
        PlaybackException.ERROR_CODE_DECODING_FORMAT_UNSUPPORTED -> "playback_format"
        PlaybackException.ERROR_CODE_DECODER_INIT_FAILED,
        PlaybackException.ERROR_CODE_DECODER_QUERY_FAILED,
        PlaybackException.ERROR_CODE_DECODING_FAILED,
        PlaybackException.ERROR_CODE_DECODING_FORMAT_EXCEEDS_CAPABILITIES -> "playback_decoder"
        else -> "playback_unknown"
    }
    return errorText(code, "code" to error.errorCode)
}
