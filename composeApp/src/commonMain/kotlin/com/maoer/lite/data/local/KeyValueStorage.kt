package com.maoer.lite.data.local

/**
 * 简单的键值对存储的平台无关接口。
 * 
 * 用于跨应用程序重新启动持久化少量用户数据（如上次播放的音频、播放进度）。
 * 
 * 实现：
 * - Android: SharedPreferences
 * - iOS: NSUserDefaults
 */
expect class KeyValueStorage() {
    fun saveString(key: String, value: String)
    fun getString(key: String): String?
}