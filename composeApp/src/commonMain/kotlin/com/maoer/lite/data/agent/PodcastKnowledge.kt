package com.maoer.lite.data.agent

import com.maoer.lite.data.podcast.*
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.serialization.json.*
import kotlin.math.ln

/** RSS evidence is retrieved on the device; only bounded excerpts reach the model. */
class PodcastKnowledge(private val repository: PodcastRepository) {
    private val refreshSlots = Semaphore(3)

    private data class Loaded(val source: PodcastSource, val feed: PodcastFeed?, val fresh: Boolean)

    private suspend fun load(source: PodcastSource): Loaded = refreshSlots.withPermit {
        val refreshed = withTimeoutOrNull(10_000) {
            try { repository.refresh(source.id) }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { null }
        }
        Loaded(source, refreshed ?: repository.cached(source.id), refreshed != null)
    }

    suspend fun details(id: String): JsonObject {
        val loaded = load(repository.source(id))
        val feed = loaded.feed ?: return buildJsonObject {
            put("status", "error"); put("note", "节目资料暂时无法读取，不能据此编写推荐理由。")
        }
        return buildJsonObject {
            put("status", "ok")
            put("description_scope", "podcast_description_excerpt_not_episode_descriptions")
            put("episode_full_description_available", false)
            put("note", if (loaded.fresh) "依据来自本次核对的 RSS 节目简介。" else "刷新失败，依据来自缓存；更新时间未知。")
            putJsonArray("items") { add(buildJsonObject {
                put("kind", "podcast"); put("id", id); put("title", feed.title.take(300))
                put("category", loaded.source.category); put("author", feed.author.take(200))
                put("catalog_summary", agentCatalogSummary(feed))
                evidence(loaded, "节目简介", feed.description.take(2400), feed.description.length > 2400)
            }) }
        }
    }

    suspend fun search(keywords: List<String>, podcastId: String = ""): JsonObject {
        require(keywords.size in 1..4 && keywords.all { it.trim().length in 2..30 })
        val terms = keywords.map { it.trim().lowercase() }.distinct()
        val sources = if (podcastId.isBlank()) repository.sources else listOf(repository.source(podcastId))
        // Keep only eight matching episodes per source in the cross-feed merge.
        // Retaining all 30 parsed feeds and lowercase copies can exhaust a phone heap.
        val searched = coroutineScope { sources.map { source -> async {
            val item = load(source)
            withContext(Dispatchers.Default) {
                val matches = rankKnowledge(item.feed?.episodes.orEmpty().map {
                    KnowledgeDocument(source.id, it.id, it.title, it.description)
                }, terms)
                val ids = matches.take(8).map { it.document.episodeId }.toSet()
                item.copy(feed = item.feed?.copy(episodes = item.feed.episodes.filter { it.id in ids })) to matches.size
            }
        } }.awaitAll() }
        val loaded = searched.map { it.first }
        val hits = withContext(Dispatchers.Default) {
            rankKnowledge(loaded.flatMap { item -> item.feed?.episodes.orEmpty().map { episode ->
                KnowledgeDocument(item.source.id, episode.id, episode.title, episode.description)
            } }, terms)
        }
        val bySource = loaded.associateBy { it.source.id }
        return buildJsonObject {
            put("status", "ok")
            put("coverage_total", sources.size)
            put("coverage_available", loaded.count { it.feed != null })
            put("coverage_fresh", loaded.count { it.fresh })
            put("total_matches", searched.sumOf { it.second })
            put("returned_matches", hits.take(4).size)
            put("omitted_matches", searched.sumOf { it.second } - hits.take(4).size)
            putJsonArray("searched_keywords") { terms.forEach { add(it) } }
            put("match_operator", "ALL_keywords_in_same_episode")
            put("negative_claim_supported", false)
            put("search_scope", "available_rss_episode_titles_and_descriptions_literal_all_keywords")
            put("evidence_scope", "at_most_4_matching_episodes_description_excerpts_up_to_900_chars_each")
            put("episode_full_description_available", false)
            put("audio_analyzed", false)
            put("note", "已检索 ${loaded.count { it.feed != null }}/${sources.size} 个节目的可用 RSS 分集，" +
                "其中 ${loaded.count { it.fresh }} 个本次更新核对成功。" +
                if (hits.isEmpty()) "未找到在同一条标题和简介中同时包含全部关键词【${terms.joinToString("、")}】的资料；不能据此说其中任何单个词不存在，更不能证明没有广告或音频没涉及。" else "仅展示最多4条命中文案的节选，未分析音频；不能用这些片段断言全部RSS没有某种标注。")
            putJsonArray("items") {
                hits.take(4).forEach { hit ->
                    val item = bySource.getValue(hit.document.podcastId)
                    val episode = item.feed!!.episodes.first { it.id == hit.document.episodeId }
                    add(buildJsonObject {
                        put("kind", "episode"); put("id", episode.id); put("podcast_id", item.source.id)
                        put("podcast_title", item.feed.title.take(150)); put("title", episode.title.take(300))
                        put("published", episode.published.take(150)); put("duration_seconds", episode.durationSeconds)
                        putJsonArray("matched_keywords") { terms.forEach { add(it) } }
                        evidence(item, "分集标题与简介", knowledgeExcerpt(episode.description, terms), episode.description.length > 900)
                    })
                }
            }
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

data class KnowledgeDocument(val podcastId: String, val episodeId: String, val title: String, val description: String)
data class KnowledgeHit(val document: KnowledgeDocument, val score: Double)

/** All keywords must occur. IDF and saturated term frequency rank matches;
 * title boosts and source diversification avoid a long, repetitive feed dominating.
 * This is lexical retrieval, not embedding/semantic similarity.
 */
fun rankKnowledge(documents: List<KnowledgeDocument>, keywords: List<String>): List<KnowledgeHit> {
    val terms = keywords.map { it.trim().lowercase() }.filter { it.isNotEmpty() }.distinct()
    if (terms.isEmpty()) return emptyList()
    val rows = documents.distinctBy { it.episodeId }.map { document ->
        val text = (document.title + " " + document.description).lowercase()
        document to terms.map { term ->
            var count = 0; var offset = text.indexOf(term)
            while (offset >= 0 && count < 8) { count++; offset = text.indexOf(term, offset + term.length) }
            count
        }
    }
    val idf = terms.indices.map { i -> ln(1.0 + rows.size.toDouble() / (1 + rows.count { it.second[i] > 0 })) }
    val ranked = rows.mapNotNull { (document, counts) ->
        if (counts.any { it == 0 }) return@mapNotNull null
        val score = terms.indices.sumOf { i ->
            val count = counts[i]
            idf[i] * (count.toDouble() / (count + 1) + if (terms[i] in document.title.lowercase()) 1.5 else 0.0)
        }
        KnowledgeHit(document, score)
    }.sortedWith(compareByDescending<KnowledgeHit> { it.score }.thenBy { it.document.episodeId })
    // Prefer one result per show before additional episodes from the same show.
    val first = ranked.distinctBy { it.document.podcastId }
    val selected = first.map { it.document.episodeId }.toSet()
    return first + ranked.filter { it.document.episodeId !in selected }
}

/** Literal source excerpts, never model-written summaries. */
fun knowledgeExcerpt(text: String, keywords: List<String>, limit: Int = 900): String {
    if (text.length <= limit) return text
    val lower = text.lowercase()
    val positions = keywords.mapNotNull { term -> lower.indexOf(term.lowercase()).takeIf { it >= 0 } }.distinct().sorted()
    if (positions.isEmpty()) return text.take(limit).let { excerpt ->
        if (excerpt.isEmpty()) excerpt else excerpt.dropLast(1) + "…"
    }
    val piece = (limit - positions.size * 6) / positions.size
    val ranges = positions.map { position ->
        val start = (position - piece / 3).coerceAtLeast(0)
        start until (start + piece).coerceAtMost(text.length)
    }
    return ranges.joinToString(" … ") { range -> text.substring(range.first, range.last + 1) }.take(limit)
}
