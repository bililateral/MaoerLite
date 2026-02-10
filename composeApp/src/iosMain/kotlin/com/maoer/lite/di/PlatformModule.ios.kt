package com.maoer.lite.di

import com.maoer.lite.data.manager.IosMediaPlayerController
import com.maoer.lite.data.manager.MediaPlayerController
import org.koin.dsl.module

actual val platformModule = module {
    single<MediaPlayerController> { IosMediaPlayerController() }
}
