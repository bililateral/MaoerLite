package com.maoer.lite.data.agent

import com.maoer.lite.data.error.errorText
import com.maoer.lite.data.error.failureText
import com.maoer.lite.data.error.rssFailureCode
import com.maoer.lite.data.podcast.*
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.serialization.json.*

/** Read podcast metadata and bounded RSS evidence on the device. */
class PodcastKnowledge(private val repository: PodcastRepository) {
    private val refreshSlots = Semaphore(3)

    private data class Loaded(val source: PodcastSource, val feed: PodcastFeed?, val fresh: Boolean,
                              val errorCode: String = "", val errorMessage: String = "")

    private suspend fun load(source: PodcastSource): Loaded = refreshSlots.withPermit {
        var code = ""
        var message = ""
        val refreshed = withTimeoutOrNull(10_000) {
            try { repository.refresh(source.id) }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { code = rssFailureCode(e); message = failureText(e, code); null }
        }
        if (refreshed == null && code.isEmpty()) { code = "rss_request_timeout"; message = errorText(code) }
        Loaded(source, refreshed ?: repository.cached(source.id), refreshed != null, code, message)
    }

    suspend fun details(id: String): JsonObject {
        val loaded = load(repository.source(id))
        val feed = loaded.feed ?: return buildJsonObject {
            put("status", "error"); put("code", loaded.errorCode); put("note", loaded.errorMessage)
        }
        return buildJsonObject {
            put("status", "ok")
            put("description_scope", "podcast_description_excerpt_not_episode_descriptions")
            put("episode_full_description_available", false)
            put("note", if (loaded.fresh) "依据来自本次核对的 RSS 节目简介。" else errorText("rss_cached_notice", "message" to loaded.errorMessage))
            putJsonArray("items") { add(buildJsonObject {
                put("kind", "podcast"); put("id", id); put("title", feed.title.take(300))
                put("category", loaded.source.category); put("author", feed.author.take(200))
                put("catalog_summary", agentCatalogSummary(feed))
                evidence(loaded, "节目简介", feed.description.take(2400), feed.description.length > 2400)
            }) }
        }
    }

    private fun JsonObjectBuilder.evidence(loaded: Loaded, field: String, excerpt: String, truncated: Boolean) {
        put("evidence", excerpt)
        put("evidence_field", field)
        put("evidence_truncated", truncated)
        put("source_url", loaded.source.feedUrl)
        put("freshness", if (loaded.fresh) "refreshed" else "cached")
        put("evidence_available", excerpt.isNotBlank())
    }
}
