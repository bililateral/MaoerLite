package com.maoer.lite

import android.app.Application
import android.content.Context

class MaoerApplication : Application() {
    companion object {
        lateinit var instance: MaoerApplication
            private set
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
    }
}