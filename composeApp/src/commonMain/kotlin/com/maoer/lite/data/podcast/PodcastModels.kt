package com.maoer.lite.data.podcast

import com.maoer.lite.data.model.Audio
import kotlinx.serialization.Serializable

@Serializable
data class PodcastSource(
    val id: String,
    val title: String,
    val feedUrl: String,
    val coverUrl: String,
    val category: String,
    val website: String,
    val downloadAllowed: Boolean = false,
)

@Serializable
data class PodcastEpisode(
    val id: String,
    val title: String,
    val audioUrl: String,
    val description: String = "",
    val coverUrl: String = "",
    val published: String = "",
    val durationSeconds: Long = 0,
) {
    fun asAudio(podcast: PodcastFeed) = Audio(
        id = id, title = title, author = podcast.title,
        coverUrl = coverUrl.ifBlank { podcast.coverUrl },
        duration = formatDuration(durationSeconds), audioUrl = audioUrl,
        description = description, podcastId = podcast.sourceId,
    )
}

@Serializable
data class PodcastFeed(
    val sourceId: String,
    val title: String,
    val author: String,
    val description: String,
    val coverUrl: String,
    val episodes: List<PodcastEpisode>,
)

@Serializable
data class CachedFeed(val feed: PodcastFeed, val etag: String? = null, val modified: String? = null)

fun formatDuration(seconds: Long): String {
    val value = seconds.coerceAtLeast(0)
    val minutes = (value / 60 % 60).toString().padStart(2, '0')
    val remainder = (value % 60).toString().padStart(2, '0')
    return if (value >= 3600) "${value / 3600}:$minutes:$remainder" else "${value / 60}:$remainder"
}
