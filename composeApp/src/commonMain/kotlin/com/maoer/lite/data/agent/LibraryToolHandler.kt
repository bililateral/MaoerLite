package com.maoer.lite.data.agent

import com.maoer.lite.data.download.Downloads
import com.maoer.lite.data.download.DownloadStatus
import com.maoer.lite.data.library.ListeningLibrary
import com.maoer.lite.data.library.LibraryQueries
import com.maoer.lite.data.podcast.PodcastRepository
import com.maoer.lite.data.podcast.PodcastSearchIndex
import kotlinx.serialization.json.*

internal class LibraryToolHandler(
    private val repository: PodcastRepository,
    private val library: ListeningLibrary,
    private val downloads: Downloads,
) {
    private val search = PodcastSearchIndex(repository.sources)
    suspend fun perform(call: AgentCall): JsonObject {
        val args = call.arguments
        require(call.name == "query_library" && args.keys.all { it in setOf("tab", "favorite_kind", "query", "offset") })
        val tab = args.getValue("tab").jsonPrimitive.content
        val type = args["favorite_kind"]?.jsonPrimitive?.content ?: "all"
        val query = args["query"]?.jsonPrimitive?.content.orEmpty().trim()
        val offset = args["offset"]?.jsonPrimitive?.int ?: 0
        require(tab in listOf("continue", "favorites", "history", "downloads") && type in listOf("all", "podcasts", "episodes"))
        require(query.length <= 100 && offset in 0..100000 && (tab == "favorites" || type == "all"))
        library.awaitReady()
        val matching = search.search(query).map { it.id }.toSet()
        val entries = library.entries.value
        val bookmarks = library.bookmarks.value
        val rows = mutableListOf<JsonObject>()
        fun episode(id: String, title: String, podcast: String, extra: JsonObject) = buildJsonObject {
            put("kind", "episode"); put("id", id); put("title", title.take(160)); put("podcast_id", podcast)
            put("podcast_title", repository.sources.find { it.id == podcast }?.title?.take(80).orEmpty())
            extra.forEach { (key, value) -> put(key, value) }
        }
        if (tab == "downloads") {
            downloads.awaitReady()
            downloads.items.value.filter { LibraryQueries.matches(it.audio, query, matching) }.forEach { item ->
                rows += episode(item.audio.id, item.audio.title, item.audio.podcastId, buildJsonObject {
                    put("download_status", item.status.name.lowercase()); put("bytes", item.bytes); put("total_bytes", item.total)
                    put("offline_available", item.status == DownloadStatus.COMPLETE && downloads.localUri(item.audio.id) != null)
                })
            }
        } else {
            if (tab == "favorites" && type != "episodes") {
                LibraryQueries.bookmarks(repository.sources, bookmarks, matching).forEach { source -> rows += buildJsonObject {
                    put("kind", "podcast"); put("id", source.id); put("title", source.title.take(150)); put("category", source.category)
                } }
            }
            if (tab != "favorites" || type != "podcasts") {
                val index = listOf("continue", "favorites", "history").indexOf(tab)
                LibraryQueries.entries(entries, index, query, matching).forEach { entry ->
                    rows += episode(entry.audio.id, entry.audio.title, entry.audio.podcastId, buildJsonObject {
                        put("position_ms", entry.positionMs); put("duration_ms", entry.durationMs); put("last_played_at", entry.lastPlayedAt)
                        put("completed", entry.completed); put("favorite", entry.favorite)
                    })
                }
            }
        }
        var page = rows.drop(offset).take(8)
        fun response() = buildJsonObject {
            put("status", "ok"); put("tab", tab); put("favorite_kind", type); put("query", query)
            put("total", rows.size); put("returned", page.size); put("offset", offset)
            put("has_more", offset + page.size < rows.size)
            put("next_offset", if (offset + page.size < rows.size) offset + page.size else 0)
            put("snapshot_scope", "手机本地资料库。收藏节目按内置目录顺序，分集按页面收听顺序。")
            putJsonArray("items") { page.forEach(::add) }
        }
        while (page.isNotEmpty() && response().toString().encodeToByteArray().size > 10000) page = page.dropLast(1)
        check(page.isNotEmpty() || offset >= rows.size) { "Library result exceeds protocol limit" }
        return response()
    }
}
