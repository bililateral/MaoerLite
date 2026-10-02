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
    private fun error(note: String, reason: String = "tool_unavailable") = buildJsonObject {
        put("status", "error"); put("reason", reason); put("note", note)
    }
    suspend fun execute(run: String, call: AgentCall, conversationId: String = ""): AgentReceipt {
        val key = "$run:${call.id}".encodeUtf8().sha256().hex()
        val fingerprint = "${call.name}:${call.arguments}".encodeUtf8().sha256().hex()
        val old = storage.execution(key)
        if (old != null) {
            require(old.fingerprint == fingerprint) { "工具 ID 对应的参数发生变化" }
            val result = old.result ?: state("unknown", "上次执行中断，已查询当前状态；为避免重复操作，不重新执行。")
            storage.saveExecution(key, old.copy(result = result, conversationId = conversationId, runId = run))
            return AgentReceipt(call.id, result)
        }
        // Claim has no device side effect and is safe to repeat if its response
        // is lost. Do not persist an execution marker until authorization succeeds.
        api.claim(run, call.id)
        val execution = AgentExecution(fingerprint, conversationId = conversationId, runId = run)
        storage.saveExecution(key, execution)
        var result: JsonObject
        try {
            result = withContext(Dispatchers.Main) { perform(call) }
        } catch (cancelled: CancellationException) {
            withContext(NonCancellable) {
                storage.saveExecution(key, execution.copy(result = state("unknown", "任务已停止，操作可能已生效；未重复执行。")))
            }
            throw cancelled
        } catch (_: Exception) {
            result = if (call.name in setOf("list_episodes", "get_podcast_details", "search_podcast_content"))
                error("RSS资料读取或解析未完成，不表示节目不存在或分集越界；请稍后重查。", "rss_load_failed")
            else if (call.name == "search_catalog") error("节目目录查询未完成，请核对查询条件。", "catalog_query_failed")
            else state("unknown", "本次操作未能确认完成；以下为实际播放器状态，不要重复执行控制操作。").let { snapshot ->
                buildJsonObject { snapshot.forEach { (key, value) -> put(key, value) }; put("reason", "operation_unconfirmed") }
            }
        }
        storage.saveExecution(key, execution.copy(result = result))
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
                    put("returned", minOf(matches.size, 8)); put("omitted", (matches.size - 8).coerceAtLeast(0))
                    put("has_more", matches.size > 8)
                    put("query", query); put("search_scope", "内置节目名称、分类及其拼音索引，不搜索RSS正文")
                    if (repository.sources.any { it.category == query.trim() }) {
                        put("exact_category", query.trim())
                        put("category_total", repository.sources.count { it.category == query.trim() })
                        put("note", "该分类数量已完整统计；不足时如实说明，不用同义词或拼音重复查找凑数，扩大类别需先询问用户。")
                    }
                    putJsonArray("items") { matches.take(8).forEach { source -> add(buildJsonObject {
                        put("kind", "podcast"); put("id", source.id); put("title", source.title); put("category", source.category)
                    }) } }
                }
            }
            "list_episodes" -> {
                val id = args.getValue("podcast_id").jsonPrimitive.content
                repository.source(id)
                // Explicit refresh: latest episodes must not silently use a stale cache.
                val feed = repository.refresh(id)
                episodeSelection(feed, args)
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
