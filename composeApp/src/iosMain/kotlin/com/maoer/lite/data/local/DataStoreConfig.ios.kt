package com.maoer.lite.data.local

import platform.Foundation.NSDocumentDirectory
import platform.Foundation.NSFileManager
import platform.Foundation.NSURL
import platform.Foundation.NSUserDomainMask

actual fun getDataStorePath(): String {
    // iOS 端把 DataStore 放到 Documents 目录（Demo）。
    // 生产化可考虑 Library/Application Support，并配合 iCloud/备份策略。
    val documentDirectory: NSURL? = NSFileManager.defaultManager.URLForDirectory(
        directory = NSDocumentDirectory,
        inDomain = NSUserDomainMask,
        appropriateForURL = null,
        create = false,
        error = null,
    )
    return requireNotNull(documentDirectory).path + "/$DATA_STORE_FILE_NAME"
}
