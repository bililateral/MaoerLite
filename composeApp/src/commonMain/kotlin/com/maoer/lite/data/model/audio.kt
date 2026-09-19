package com.maoer.lite.data.model

import kotlinx.serialization.Serializable

@Serializable
data class Audio(
    // 业务唯一 ID（Demo 中用字符串 "1"/"2"...）
    val id: String,
    val title: String,
    val author: String,
    val coverUrl: String,
    // UI 展示用时长（字符串）。真实播放器的 duration 以 Media3/AVPlayer 为准。
    val duration: String = "00:00",
    val audioUrl: String,
    val description: String = "",
    val podcastId: String = ""
)
