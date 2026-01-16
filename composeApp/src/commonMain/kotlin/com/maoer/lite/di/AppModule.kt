package com.maoer.lite.di

import com.maoer.lite.data.local.KeyValueStorage
import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.data.repository.MaoerRepository
import com.maoer.lite.ui.home.HomeViewModel
import io.ktor.client.HttpClient
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.serialization.kotlinx.json.json
import kotlinx.serialization.json.Json
import org.koin.dsl.module

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
    single { KeyValueStorage() }
    
    // Repository
    single { MaoerRepository() }
    
    // Player Manager
    single { PlayerManager(get(), get()) }
    
    // ScreenModel (ViewModel)
    factory { HomeViewModel(get()) }
}