package com.maoer.lite.di

import com.maoer.lite.data.local.KeyValueStorage
import com.maoer.lite.data.local.getAppDataStore
import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.data.library.ListeningLibrary
import com.maoer.lite.data.library.FileLibraryPersistence
import com.maoer.lite.data.podcast.*
import io.ktor.client.plugins.HttpTimeout
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
            install(HttpTimeout) { requestTimeoutMillis = 30_000; connectTimeoutMillis = 10_000; socketTimeoutMillis = 15_000 }
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
    // 内置真实 RSS 目录与按节目隔离的持久缓存。
    single<PodcastCache> { FilePodcastCache() }
    single { RssParser() }
    single { PodcastRepository(get(), get(), get()) }
    single { ListeningLibrary(FileLibraryPersistence()) }
    
    // Player Manager
    // 全局播放器状态管理器：跨页面共享（首页底栏/详情页共用）。
    single { PlayerManager(get(), get(), get(), get(), get()) }
    
    // ScreenModel (ViewModel)
    factory { HomeViewModel(get()) }
}
