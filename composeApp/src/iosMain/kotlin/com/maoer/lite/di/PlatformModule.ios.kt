package com.maoer.lite.di

import com.maoer.lite.data.manager.IosMediaPlayerController
import com.maoer.lite.data.manager.MediaPlayerController
import com.maoer.lite.data.download.Downloads
import com.maoer.lite.data.download.UnavailableDownloads
import org.koin.dsl.module

actual val platformModule = module {
    single<Downloads> { UnavailableDownloads() }
    single<MediaPlayerController> { IosMediaPlayerController() }
}
