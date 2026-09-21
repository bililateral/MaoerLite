package com.maoer.lite.data.agent

import com.maoer.lite.MaoerApplication

actual fun agentStoragePath(): String = MaoerApplication.instance.noBackupFilesDir.resolve("agent").absolutePath

actual fun agentPublishedMillis(value: String): Long? {
    val text = value.trim()
    for (pattern in listOf("EEE, dd MMM yyyy HH:mm:ss Z", "dd MMM yyyy HH:mm:ss Z", "yyyy-MM-dd'T'HH:mm:ssXXX", "yyyy-MM-dd'T'HH:mm:ss.SSSXXX")) {
        val format = java.text.SimpleDateFormat(pattern, java.util.Locale.US).apply { isLenient = false; timeZone = java.util.TimeZone.getTimeZone("UTC") }
        val position = java.text.ParsePosition(0)
        val date = format.parse(text, position)
        if (date != null && position.index == text.length) return date.time
    }
    return null
}
