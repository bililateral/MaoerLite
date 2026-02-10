package com.maoer.lite

import android.app.Application

class MaoerApplication : Application() {
    companion object {
        /**
         * Application 级别 Context。
         *
         * 用途：
         * - 提供给 DataStore 路径生成（filesDir）
         * - 提供给 AndroidMediaPlayerController 创建 SessionToken/启动 Service
         *
         * 注意：这不是依赖注入的最佳实践（更推荐注入 Context），但对 Demo 来说足够直接。
         */
        lateinit var instance: MaoerApplication
            private set
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
    }
}
