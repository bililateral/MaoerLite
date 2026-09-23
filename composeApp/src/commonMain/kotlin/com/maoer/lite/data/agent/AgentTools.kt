package com.maoer.lite.data.agent

import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.data.manager.PlaybackOptions
import com.maoer.lite.data.manager.QueueNavigation
import com.maoer.lite.data.podcast.PodcastRepository
import com.maoer.lite.data.podcast.PodcastSearchIndex
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.serialization.json.*
import okio.ByteString.Companion.encodeUtf8

class AgentTools(private val repository: PodcastRepository, private val player: PlayerManager,
                 private val storage: AgentStorage, private val api: AgentApi) {
    private val search = PodcastSearchIndex(repository.sources)
    private val knowledge = PodcastKnowledge(repository)
    private fun state(status: String = "ok", note: String = "") = buildJsonObject {
        put("status", status); put("note", note)
        put("playing", player.isPlaying.value); put("buffering", player.buffering.value)
        put("playback_state", when {
            player.isPlaying.value -> "playing"
            player.buffering.value -> "buffering"
            player.playbackAudioId.value != null -> "paused"
            else -> "idle"
        })
        put("episode_id", player.playbackAudioId.value.orEmpty())
        put("title", player.currentAudio.value?.title.orEmpty())
        put("position_ms", player.positionMs.value); put("duration_ms", player.durationMs.value)
        put("playback_speed", player.playbackSpeed.value)
        put("queue_size", player.queue.value.size)
        put("queue_index", player.queue.value.indexOfFirst { it.id == player.currentAudio.value?.id })
        put("sleep_remaining_ms", player.sleepTimer.value.remainingMs)
        put("sleep_end_of_episode", player.sleepTimer.value.endOfEpisode)
        put("playback_error", player.playbackError.value.orEmpty())
    }
    private fun error(note: String) = buildJsonObject { put("status", "error"); put("note", note) }
    suspend fun execute(run: String, call: AgentCall): AgentReceipt {
        val key = "$run:${call.id}".encodeUtf8().sha256().hex()
        val fingerprint = "${call.name}:${call.arguments}".encodeUtf8().sha256().hex()
        val old = storage.execution(key)
        if (old != null) {
            require(old.fingerprint == fingerprint) { "工具 ID 对应的参数发生变化" }
            val result = old.result ?: state("unknown", "上次执行中断，已查询当前状态；为避免重复操作，不重新执行。")
            if (old.result == null) storage.saveExecution(key, AgentExecution(fingerprint, result))
            return AgentReceipt(call.id, result)
        }
        // Claim has no device side effect and is safe to repeat if its response
        // is lost. Do not persist an execution marker until authorization succeeds.
        api.claim(run, call.id)
        storage.saveExecution(key, AgentExecution(fingerprint))
        var result: JsonObject
        try {
            result = withContext(Dispatchers.Main) { perform(call) }
        } catch (cancelled: CancellationException) {
            withContext(NonCancellable) {
                storage.saveExecution(key, AgentExecution(fingerprint, state("unknown", "任务已停止，操作可能已生效；未重复执行。")))
            }
            throw cancelled
        } catch (_: Exception) {
            result = error("工具未完成，请检查网络、节目来源或实际播放状态。")
        }
        storage.saveExecution(key, AgentExecution(fingerprint, result))
        return AgentReceipt(call.id, result)
    }
    private suspend fun perform(call: AgentCall): JsonObject {
        val args = call.arguments
        return when (call.name) {
            "get_podcast_details" -> {
                require(args.keys == setOf("podcast_id"))
                knowledge.details(args.getValue("podcast_id").jsonPrimitive.content)
            }
            "search_podcast_content" -> {
                require(args.keys.all { it in setOf("keywords", "podcast_id") })
                val keywords = args.getValue("keywords").jsonArray.map { it.jsonPrimitive.content }
                knowledge.search(keywords, args["podcast_id"]?.jsonPrimitive?.content.orEmpty())
            }
            "search_catalog" -> {
                require(args.keys == setOf("query"))
                val query = args.getValue("query").jsonPrimitive.content
                require(query.length in 1..100)
                val matches = search.search(query)
                buildJsonObject {
                    put("status", "ok"); put("total", matches.size)
                    putJsonArray("items") { matches.take(8).forEach { source -> add(buildJsonObject {
                        put("kind", "podcast"); put("id", source.id); put("title", source.title); put("category", source.category)
                    }) } }
                }
            }
            "list_episodes" -> {
                require(args.keys.all { it in setOf("podcast_id", "query") })
                val id = args.getValue("podcast_id").jsonPrimitive.content
                repository.source(id)
                // Explicit refresh: latest episodes must not silently use a stale cache.
                val feed = repository.refresh(id)
                val query = args["query"]?.jsonPrimitive?.content.orEmpty()
                val matches = feed.episodes.filter { query.isBlank() || it.title.contains(query, ignoreCase = true) }
                val dates = matches.associate { it.id to agentPublishedMillis(it.published) }
                val verified = matches.isNotEmpty() && dates.values.all { it != null }
                val ordered = if (verified) matches.sortedByDescending { dates[it.id] } else matches
                buildJsonObject {
                    put("status", "ok"); put("total", matches.size); put("latest_verified", verified)
                    put("order", if (verified) "publication_desc" else "source_order_unverified")
                    putJsonArray("items") { ordered.take(8).forEach { episode -> add(buildJsonObject {
                        put("kind", "episode"); put("id", episode.id); put("podcast_id", id)
                        put("title", episode.title.take(300)); put("published", episode.published); put("duration_seconds", episode.durationSeconds)
                    }) } }
                }
            }
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
