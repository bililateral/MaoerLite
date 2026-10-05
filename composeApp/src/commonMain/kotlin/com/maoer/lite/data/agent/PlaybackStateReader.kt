package com.maoer.lite.data.agent

import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.data.podcast.PodcastRepository
import kotlinx.serialization.json.*

/** One projection of selected identity and actual player state for every tool receipt. */
internal class PlaybackStateReader(private val repository: PodcastRepository, private val player: PlayerManager) {
    fun read(status: String = "ok", note: String = "") = buildJsonObject {
        val selectedId = player.playbackAudioId.value
        // Metadata must belong to the actual selected item, including while paused.
        // During a queue transition the UI's currentAudio can briefly lag Media3.
        val selected = player.currentAudio.value?.takeIf { it.id == selectedId }
            ?: player.queue.value.firstOrNull { it.id == selectedId }
        val source = repository.sources.firstOrNull { it.id == selected?.podcastId }
        put("status", status); put("note", note)
        put("playing", player.isPlaying.value); put("buffering", player.buffering.value)
        put("playback_state", when {
            player.isPlaying.value -> "playing"
            player.buffering.value -> "buffering"
            player.playbackAudioId.value != null -> "paused"
            else -> "idle"
        })
        put("episode_id", selectedId.orEmpty())
        put("title", selected?.title.orEmpty().take(300))
        put("podcast_id", source?.id.orEmpty())
        put("podcast_title", source?.title.orEmpty())
        put("selection_identity_available", source != null && selected != null)
        put("position_ms", player.positionMs.value); put("duration_ms", player.durationMs.value)
        put("playback_speed", player.playbackSpeed.value)
        put("queue_size", player.queue.value.size)
        put("queue_index", player.queue.value.indexOfFirst { it.id == selectedId })
        put("queue_position", player.queue.value.indexOfFirst { it.id == selectedId } + 1)
        put("queue_scope", "当前播放器队列，可能经过筛选或排序；不能据此推断RSS总集数，两者数值可能相等也可能不同；队列位置不是标题期号。queue_position从1开始，0表示未在队列中找到。")
        put("sleep_remaining_ms", player.sleepTimer.value.remainingMs)
        put("sleep_end_of_episode", player.sleepTimer.value.endOfEpisode)
        put("playback_error", player.playbackError.value.orEmpty())
        put("download_available", source?.let { JsonPrimitive(it.downloadAllowed) } ?: JsonNull)
        put("download_available_sources", repository.sources.count { it.downloadAllowed })
    }
}
