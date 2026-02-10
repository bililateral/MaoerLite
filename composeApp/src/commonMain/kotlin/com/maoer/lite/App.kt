package com.maoer.lite

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import cafe.adriel.voyager.navigator.Navigator
import com.maoer.lite.di.appModule
import com.maoer.lite.di.platformModule
import com.maoer.lite.ui.home.HomeScreen
import org.koin.compose.KoinApplication

@Composable
fun App() {
    /**
     * 应用根 Composable。
     *
     * 这里做两件事：
     * 1. 初始化 Koin（将 commonMain 的 appModule + 平台侧 platformModule 注入到同一个容器）。
     * 2. 初始化 Voyager 导航，并设置首页为根 Screen。
     */
    KoinApplication(application = {
        modules(appModule, platformModule)
    }) {
        MaterialTheme {
            // 设置 Voyager 导航根节点
            Navigator(HomeScreen)
        }
    }
}
