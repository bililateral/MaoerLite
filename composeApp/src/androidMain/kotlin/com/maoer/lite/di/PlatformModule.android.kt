package com.maoer.lite.di

import com.maoer.lite.data.manager.AndroidMediaPlayerController
import com.maoer.lite.data.manager.MediaPlayerController
import org.koin.dsl.module

actual val platformModule = module {
    // Android 端注入真实的播放控制器：通过 MediaController 连接后台 Media3 Service。
    single<MediaPlayerController> { AndroidMediaPlayerController() }
}
