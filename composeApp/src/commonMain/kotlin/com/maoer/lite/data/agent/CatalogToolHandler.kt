package com.maoer.lite.data.agent

import com.maoer.lite.data.podcast.PodcastRepository
import com.maoer.lite.data.podcast.PodcastSearchIndex
import kotlinx.serialization.json.*

internal class CatalogToolHandler(private val repository: PodcastRepository) {
    private val search = PodcastSearchIndex(repository.sources)
    private val knowledge = PodcastKnowledge(repository)
    suspend fun perform(call: AgentCall): JsonObject {
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
            else -> agentToolError("不支持此工具。")
        }
    }
    companion object {
        val names = setOf("get_podcast_details", "search_podcast_content", "search_catalog", "list_episodes")
    }
}
