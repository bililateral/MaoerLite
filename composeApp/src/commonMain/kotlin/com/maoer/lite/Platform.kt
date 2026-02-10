package com.maoer.lite

interface Platform {
    // 平台名称，用于 Demo 展示/调试
    val name: String
}

// expect/actual：各平台提供自己的 Platform 实现
expect fun getPlatform(): Platform
