package com.maoer.lite.data.agent

import com.maoer.lite.data.podcast.PodcastFeed
import kotlinx.serialization.json.*

private val titleNumberPattern = Regex("^(?:第\\s*|(?:ep|vol|no)[\\s.．:：\\-–—]*|#\\s*)?(\\d+)(?=\\D|$)", RegexOption.IGNORE_CASE)
internal fun agentEpisodeNumber(title: String): Int? = titleNumberPattern.find(title.trim())?.groupValues?.get(1)
    ?.toIntOrNull()?.takeIf { it in 1..100000 }

/** Full RSS list positions, publisher title numbers and filtered pages are different concepts. */
internal fun episodeSelection(feed: PodcastFeed, args: JsonObject): JsonObject {
    require(args.keys.all { it in setOf("podcast_id", "query", "order", "offset", "position", "episode_number") })
    val query = args["query"]?.jsonPrimitive?.content.orEmpty().trim()
    val order = args["order"]?.jsonPrimitive?.content ?: "newest"
    val offset = args["offset"]?.jsonPrimitive?.int ?: 0
    val position = args["position"]?.jsonPrimitive?.int ?: 0
    val number = args["episode_number"]?.jsonPrimitive?.int ?: 0
    require(query.length <= 100 && order in setOf("newest", "source", "reverse_source"))
    require(listOf(offset, position, number).all { it in 0..100000 })
    require(listOf(query.isNotEmpty(), position > 0, number > 0).count { it } <= 1)
    require(position == 0 || offset == 0)
    val dates = feed.episodes.associate { it.id to agentPublishedMillis(it.published) }
    val datesVerified = feed.episodes.isNotEmpty() && dates.values.all { it != null }
    if (order == "newest" && position > 0 && !datesVerified) return buildJsonObject {
        put("status", "error"); put("note", "部分发布日期无法确认，不能按从新到旧的第N条定位；请选择原始列表顺序或标题。")
        put("feed_total", feed.episodes.size)
    }
    val ordered = when (order) {
        "source" -> feed.episodes
        "reverse_source" -> feed.episodes.reversed()
        else -> if (datesVerified) feed.episodes.sortedByDescending { dates[it.id] } else feed.episodes
    }
    val matches = when {
        position > 0 -> ordered.getOrNull(position - 1)?.let { listOf(it) }.orEmpty()
        number > 0 -> ordered.filter { agentEpisodeNumber(it.title) == number }
        else -> ordered.filter { query.isEmpty() || it.title.contains(query, ignoreCase = true) }
    }
    val page = matches.drop(offset).take(8)
    val sourcePositions = feed.episodes.mapIndexed { index, e -> e.id to index + 1 }.toMap()
    val orderedPositions = ordered.mapIndexed { index, e -> e.id to index + 1 }.toMap()
    return buildJsonObject {
        put("status", "ok"); put("total", matches.size); put("feed_total", feed.episodes.size)
        put("matched_total", matches.size); put("offset", offset); put("returned", page.size)
        put("has_more", offset + page.size < matches.size)
        put("next_offset", if (offset + page.size < matches.size) offset + page.size else -1)
        put("selection", when { position > 0 -> "list_position"; number > 0 -> "title_number"; query.isNotEmpty() -> "title_keyword"; else -> "page" })
        put("order", when { order == "source" -> "source"; order == "reverse_source" -> "reverse_source"; datesVerified -> "publication_desc"; else -> "source_order_unverified" })
        put("latest_verified", order == "newest" && datesVerified && query.isEmpty() && number == 0 && position in 0..1 && offset == 0)
        put("note", when {
            number > 0 && matches.isEmpty() -> "未找到标题期号匹配；不表示列表第${number}条不存在，总条目数也不等于最大期号。"
            position > ordered.size -> "指定排序的第${position}条超出本次RSS列表范围；共有${ordered.size}条。"
            query.isNotEmpty() && matches.isEmpty() -> "整个RSS列表中没有标题关键词匹配；未搜索音频全文，也不能据此否定列表位置。"
            else -> "列表位置从1开始，source_position对应节目页原始顺序；标题期号由作者命名，两者可能不同。"
        })
        putJsonArray("items") { page.forEach { episode -> add(buildJsonObject {
            put("kind", "episode"); put("id", episode.id); put("podcast_id", feed.sourceId)
            put("title", episode.title.take(300)); put("published", episode.published)
            put("duration_seconds", episode.durationSeconds)
            put("source_position", sourcePositions.getValue(episode.id)); put("ordered_position", orderedPositions.getValue(episode.id))
            agentEpisodeNumber(episode.title)?.let { put("title_episode_number", it) }
        }) } }
    }
}
