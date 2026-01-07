package com.maoer.lite

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import cafe.adriel.voyager.navigator.Navigator
import com.maoer.lite.di.appModule
import com.maoer.lite.ui.home.HomeScreen
import org.koin.compose.KoinApplication

@Composable
fun App() {
    // 注入 Koin
    KoinApplication(application = {
        modules(appModule)
    }) {
        MaterialTheme {
            // 设置 Voyager 导航根节点
            Navigator(HomeScreen)
        }
    }
}