package com.maoer.lite.data.local

import android.content.Context
import com.maoer.lite.MaoerApplication

actual class KeyValueStorage {
    private val prefs by lazy {
        MaoerApplication.instance.getSharedPreferences("maoer_lite_prefs", Context.MODE_PRIVATE)
    }

    actual fun saveString(key: String, value: String) {
        prefs.edit().putString(key, value).apply()
    }

    actual fun getString(key: String): String? {
        return prefs.getString(key, null)
    }
}