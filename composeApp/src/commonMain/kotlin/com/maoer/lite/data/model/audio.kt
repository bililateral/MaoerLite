package com.maoer.lite.data.model

import kotlinx.serialization.Serializable

@Serializable
data class Audio(
    val id: String,
    val title: String,
    val author: String,
    val coverUrl: String,
    val duration: String = "00:00"
)