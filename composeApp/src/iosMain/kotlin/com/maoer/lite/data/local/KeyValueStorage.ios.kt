package com.maoer.lite.data.local

import platform.Foundation.NSUserDefaults

actual class KeyValueStorage {
    private val defaults = NSUserDefaults.standardUserDefaults

    actual fun saveString(key: String, value: String) {
        defaults.setObject(value, forKey = key)
    }

    actual fun getString(key: String): String? {
        return defaults.stringForKey(key)
    }
}