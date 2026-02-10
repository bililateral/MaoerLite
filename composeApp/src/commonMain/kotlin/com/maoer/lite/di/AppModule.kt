package com.maoer.lite.di

import com.maoer.lite.data.local.KeyValueStorage
import com.maoer.lite.data.local.getAppDataStore
import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.data.repository.MaoerRepository
import com.maoer.lite.ui.home.HomeViewModel
import io.ktor.client.HttpClient
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.serialization.kotlinx.json.json
import kotlinx.serialization.json.Json
import org.koin.dsl.module

/**
 * Koin 依赖注入模块（与平台无关的 commonMain 部分）。
 *
 * 约定：
 * - “全局单例”用 single，例如 Repository / PlayerManager / DataStore。
 * - “页面级对象”用 factory，例如 ScreenModel(ViewModel)。
 */
val appModule = module {
    // Ktor Client
    single {
        HttpClient {
            install(ContentNegotiation) {
                json(Json { ignoreUnknownKeys = true })
            }
        }
    }
    
    // Storage
    // DataStore：同一进程内同一文件必须是单例，否则会抛异常。
    single { getAppDataStore() }
    single { KeyValueStorage(get()) }
    
    // Repository
    // 目前是 Mock 数据仓库；未来可替换为真实网络请求实现。
    single { MaoerRepository() }
    
    // Player Manager
    // 全局播放器状态管理器：跨页面共享（首页底栏/详情页共用）。
    single { PlayerManager(get(), get()) }
    
    // ScreenModel (ViewModel)
    factory { HomeViewModel(get()) }
}
