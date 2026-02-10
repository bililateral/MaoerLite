package com.maoer.lite.data.local

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.flow.first

/**
 * 基于 DataStore 的键值对存储。
 *
 * 替代了原有的 SharedPreferences/NSUserDefaults 实现。
 *
 * 当前实现仅提供 String 存取，原因：
 * - Demo 阶段只需要保存 `last_audio_id` / `last_audio_progress` 等少量字段；
 * - 用 String 可以避免多端类型差异与迁移成本。
 *
 * 生产化建议：
 * - 把 key 定义成常量/枚举，避免散落的字符串字面量；
 * - 对频繁写入（例如每 200ms 保存进度）要做降频，否则会带来不必要的 IO；
 * - 复杂结构建议改用 proto DataStore 或数据库（Room/SQLDelight）。
 */
class KeyValueStorage(private val dataStore: DataStore<Preferences>) {

    suspend fun saveString(key: String, value: String) {
        dataStore.edit { preferences ->
            preferences[stringPreferencesKey(key)] = value
        }
    }

    suspend fun getString(key: String): String? {
        val preferences = dataStore.data.first()
        return preferences[stringPreferencesKey(key)]
    }
}
