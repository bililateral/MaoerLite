package com.maoer.lite.data.agent

import com.maoer.lite.data.error.errorText
import com.maoer.lite.data.error.failureText
import com.maoer.lite.data.error.rssFailureCode
import kotlinx.coroutines.CancellationException
import com.maoer.lite.data.podcast.PodcastRepository
import com.maoer.lite.data.podcast.PodcastSearchIndex
import kotlinx.serialization.json.*

internal class CatalogToolHandler(private val repository: PodcastRepository) {
    private val search = PodcastSearchIndex(repository.sources)
    private val knowledge = PodcastKnowledge(repository)
    suspend fun perform(call: AgentCall): JsonObject = try {
        performRequest(call)
    } catch (cancelled: CancellationException) {
        throw cancelled
    } catch (error: Exception) {
        val code = if (call.name in setOf("list_episodes", "get_podcast_details")) rssFailureCode(error) else "catalog_query_failed"
        agentToolError(if (call.name == "search_catalog") errorText(code) else failureText(error, code), code)
    }

    private suspend fun performRequest(call: AgentCall): JsonObject {
        val args = call.arguments
        return when (call.name) {
            "get_podcast_details" -> {
                require(args.keys == setOf("podcast_id"))
                knowledge.details(args.getValue("podcast_id").jsonPrimitive.content)
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
                        put("note", "该分类的节目总数见category_total。")
                    }
                    putJsonArray("items") { matches.take(8).forEach { source -> add(buildJsonObject {
                        put("kind", "podcast"); put("id", source.id); put("title", source.title); put("category", source.category)
                        put("content_relevance_verified", false)
                        put("match_note", "按名称、分类或拼音匹配；内容相关性需读取节目或分集说明")
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
            else -> agentToolError(errorText("tool_unsupported"))
        }
    }
    companion object {
        val names = setOf("get_podcast_details", "search_catalog", "list_episodes")
    }
}
