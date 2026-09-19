package com.maoer.lite

import androidx.compose.ui.window.ComposeUIViewController
import org.koin.compose.KoinApplication
import com.maoer.lite.di.appModule
import com.maoer.lite.di.platformModule

fun MainViewController() = ComposeUIViewController {
    KoinApplication(application = { modules(appModule, platformModule) }) { App() }
}
