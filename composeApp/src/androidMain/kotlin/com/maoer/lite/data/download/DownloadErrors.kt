package com.maoer.lite.data.download

import android.app.DownloadManager
import com.maoer.lite.data.error.errorText

internal fun downloadFailureMessage(reason: Int): String = when (reason) {
    DownloadManager.ERROR_INSUFFICIENT_SPACE -> errorText("download_no_space")
    DownloadManager.ERROR_DEVICE_NOT_FOUND, DownloadManager.ERROR_FILE_ERROR,
    DownloadManager.ERROR_FILE_ALREADY_EXISTS -> errorText("download_storage")
    DownloadManager.ERROR_HTTP_DATA_ERROR -> errorText("download_network")
    DownloadManager.ERROR_TOO_MANY_REDIRECTS -> errorText("download_redirects")
    DownloadManager.ERROR_UNHANDLED_HTTP_CODE -> errorText("download_http_unhandled")
    in 400..599 -> errorText("download_http", "status" to reason)
    else -> errorText("download_unknown", "code" to reason)
}

internal fun downloadWaitingMessage(reason: Int): String = errorText(when (reason) {
    DownloadManager.PAUSED_WAITING_FOR_NETWORK -> "download_wait_network"
    DownloadManager.PAUSED_QUEUED_FOR_WIFI -> "download_wait_wifi"
    DownloadManager.PAUSED_WAITING_TO_RETRY -> "download_wait_retry"
    else -> "download_wait_unknown"
})
