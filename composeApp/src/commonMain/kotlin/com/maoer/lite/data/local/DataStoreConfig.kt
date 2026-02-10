package com.maoer.lite.data.local

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.datastore.preferences.core.Preferences
import okio.Path.Companion.toPath

internal const val DATA_STORE_FILE_NAME = "maoer_lite.preferences_pb"

/**
 * 创建一个新的 DataStore 实例。
 *
 * 注意：同一进程内“同一个文件路径”只能对应一个 DataStore 实例，否则会在运行期报错。
 * 因此业务代码不应直接调用此方法，而应使用 [getAppDataStore] 获取单例。
 */
fun createDataStore(producePath: () -> String): DataStore<Preferences> {
    return PreferenceDataStoreFactory.createWithPath(
        produceFile = { producePath().toPath() }
    )
}

@Volatile
private var appDataStoreInstance: DataStore<Preferences>? = null
private val appDataStoreLock = Any()

/**
 * 获取应用级 DataStore 单例。
 *
 * DataStore 的硬约束：同一进程内同一文件只能有一个实例。
 * 这个方法用于保证 UI 进程与 Service 进程内（同一进程中不同模块）都复用同一个 DataStore。
 *
 * 如果你在别处又 new 了一个 DataStore 指向同一个文件，通常会遇到 “There are multiple DataStores active...” 之类异常。
 */
fun getAppDataStore(): DataStore<Preferences> {
    return appDataStoreInstance ?: synchronized(appDataStoreLock) {
        appDataStoreInstance ?: createDataStore { getDataStorePath() }.also { appDataStoreInstance = it }
    }
}

expect fun getDataStorePath(): String
