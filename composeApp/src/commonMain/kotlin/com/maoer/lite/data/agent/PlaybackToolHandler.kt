package com.maoer.lite.data.agent

import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.data.manager.PlaybackOptions
import com.maoer.lite.data.manager.QueueNavigation
import com.maoer.lite.data.podcast.PodcastRepository
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.serialization.json.*

internal class PlaybackToolHandler(private val repository: PodcastRepository, private val player: PlayerManager,
                                   private val reader: PlaybackStateReader) {
    private fun state(status: String = "ok", note: String = "") = reader.read(status, note)
    private fun error(note: String, reason: String = "tool_unavailable") = agentToolError(note, reason)
    suspend fun perform(call: AgentCall): JsonObject {
        val args = call.arguments
        return when (call.name) {
            "get_playback_state" -> { require(args.isEmpty()); state() }
            "play_episode" -> {
                require(args.keys == setOf("podcast_id", "episode_id"))
                val podcastId = args.getValue("podcast_id").jsonPrimitive.content
                val episodeId = args.getValue("episode_id").jsonPrimitive.content
                repository.source(podcastId)
                val feed = repository.cached(podcastId) ?: repository.refresh(podcastId)
                val episode = feed.episodes.firstOrNull { it.id == episodeId } ?: return error("该分集已不在节目目录中，请重新查询。")
                player.setPlaylist(feed.episodes.map { it.asAudio(feed) })
                player.play(episode.asAudio(feed))
                awaitPlaying(episodeId)
            }
            "pause_playback" -> {
                require(args.isEmpty()); player.pause()
                withTimeoutOrNull(3000) { player.isPlaying.first { !it } }
                val paused = !player.isPlaying.value
                state(if (paused) "ok" else "accepted", if (paused) "已确认播放暂停。" else "暂停请求已发送，等待播放器确认。")
            }
            "resume_playback" -> {
                require(args.isEmpty())
                val id = player.currentAudio.value?.id ?: return error("当前没有选中的分集，请先选择节目。")
                player.resume(); awaitPlaying(id)
            }
            "previous_episode", "next_episode" -> {
                require(args.isEmpty())
                val queue = player.queue.value
                val index = queue.indexOfFirst { it.id == player.currentAudio.value?.id }
                if (index < 0) return error("当前没有可切换的播放队列，请先选择节目播放。")
                val next = call.name == "next_episode"
                val target = queue[if (next) QueueNavigation.nextIndex(index, queue.size) else QueueNavigation.previousIndex(index, queue.size)]
                if (next) player.next() else player.previous()
                awaitPlaying(target.id)
            }
            "set_playback_speed" -> {
                require(args.keys == setOf("speed"))
                val speed = args.getValue("speed").jsonPrimitive.float
                require(speed in PlaybackOptions.speeds)
                player.setPlaybackSpeed(speed)
                val observed = withTimeoutOrNull(3000) { player.playbackSpeed.first { it == speed } } != null
                state(if (observed) "ok" else "accepted", if (observed) "已设置 ${speed.toString().removeSuffix(".0")} 倍速。" else "倍速请求已发送，等待播放器确认。")
            }
            "set_sleep_timer" -> {
                require(args.keys == setOf("minutes"))
                val minutes = args.getValue("minutes").jsonPrimitive.int
                require(minutes in -1..180)
                player.setSleepTimer(minutes.takeUnless { it == 0 })
                val observed = withTimeoutOrNull(3000) { player.sleepTimer.first { timer ->
                    when { minutes == 0 -> !timer.active; minutes == -1 -> timer.endOfEpisode
                        else -> !timer.endOfEpisode && timer.remainingMs in (minutes * 60_000L - 5000)..(minutes * 60_000L) }
                } }
                state(if (observed != null) "ok" else "accepted", when {
                    observed == null -> "定时请求已发送，等待播放器确认。"
                    minutes == 0 -> "已关闭睡眠定时。"
                    minutes == -1 -> "已设置本集播完后停止。"
                    else -> "已设置 $minutes 分钟后停止播放。"
                })
            }
            else -> error("不支持此工具。")
        }
    }
    private suspend fun awaitPlaying(id: String): JsonObject {
        val stable = withTimeoutOrNull(12000) {
            var observations = 0
            while (observations < 3) {
                delay(100)
                observations = if (player.playbackAudioId.value == id && player.isPlaying.value && !player.buffering.value) observations + 1 else 0
            }
            true
        } == true
        val snapshot = state()
        val playing = stable && snapshot.getValue("episode_id").jsonPrimitive.content == id &&
            snapshot.getValue("playing").jsonPrimitive.boolean && !snapshot.getValue("buffering").jsonPrimitive.boolean
        return buildJsonObject {
            snapshot.forEach { (key, value) -> put(key, value) }
            put("status", when { playing -> "ok"; snapshot.getValue("playback_error").jsonPrimitive.content.isNotEmpty() -> "error"; else -> "accepted" })
            put("note", if (playing) "已确认目标分集实际播放。" else "尚未确认目标分集稳定播放，请根据实际状态回答。")
        }
    }
}
