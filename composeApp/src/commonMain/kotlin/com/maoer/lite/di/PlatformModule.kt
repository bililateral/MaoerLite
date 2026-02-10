package com.maoer.lite.di

import org.koin.core.module.Module

/**
 * 平台相关的 Koin Module 声明。
 *
 * commonMain 只声明 expect，具体实现由各平台 sourceSet 提供 actual：
 * - Android: 提供真实的 Media3 播放控制器
 * - iOS: 目前为存根实现（仅用于编译通过）
 */
expect val platformModule: Module
