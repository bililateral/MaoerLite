package com.maoer.lite.data.library

import com.maoer.lite.data.model.Audio
import com.maoer.lite.data.podcast.PodcastSource

/** Shared by the screen and assistant; reading never updates listening state. */
object LibraryQueries {
    fun matches(audio: Audio, query: String, matchingPodcasts: Set<String>): Boolean =
        query.isBlank() || audio.title.contains(query.trim(), true) || audio.author.contains(query.trim(), true) || audio.podcastId in matchingPodcasts

    fun entries(entries: List<ListeningEntry>, tab: Int, query: String, matchingPodcasts: Set<String>): List<ListeningEntry> =
        entries.filter { entry -> matches(entry.audio, query, matchingPodcasts) && when (tab) {
            0 -> entry.lastPlayedAt > 0 && !entry.completed
            1 -> entry.favorite
            2 -> entry.lastPlayedAt > 0 || entry.completed
            else -> false
        } }

    fun bookmarks(sources: List<PodcastSource>, bookmarks: Set<String>, matchingPodcasts: Set<String>): List<PodcastSource> =
        sources.filter { it.id in bookmarks && it.id in matchingPodcasts }
}
