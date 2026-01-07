package com.maoer.lite

interface Platform {
    val name: String
}

expect fun getPlatform(): Platform