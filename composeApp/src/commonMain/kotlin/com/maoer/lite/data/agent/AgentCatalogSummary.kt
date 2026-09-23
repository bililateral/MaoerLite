package com.maoer.lite.data.agent

import com.maoer.lite.data.podcast.PodcastFeed
import kotlinx.serialization.json.*

/** Facts about the available RSS, never an inference about deleted/unpublished audio. */
internal fun agentCatalogSummary(feed: PodcastFeed): JsonObject {
    val numbered = feed.episodes.mapNotNull { e -> agentEpisodeNumber(e.title)?.let { it to e } }
    val counts = numbered.groupingBy { it.first }.eachCount()
    val maximum = counts.keys.maxOrNull()
    val missing = if (maximum != null) (1..maximum).filter { it !in counts } else emptyList()
    val ranges = mutableListOf<String>()
    var index = 0
    while (index < missing.size) {
        val start = missing[index]
        var end = start
        while (index + 1 < missing.size && missing[index + 1] == end + 1) { index++; end++ }
        ranges += if (start == end) "$start" else "$start–$end"
        index++
    }
    val unnumbered = feed.episodes.filter { agentEpisodeNumber(it.title) == null }
    val duplicates = counts.filterValues { it > 1 }
    return buildJsonObject {
        put("available_items", feed.episodes.size)
        put("scope", "当前可播放RSS目录全表；标题开头编号按规则识别，不代表完整发布历史或作者正式编号体系。")
        put("numbered_items", numbered.size); put("unique_title_numbers", counts.size)
        put("min_title_number", counts.keys.minOrNull()?.let { JsonPrimitive(it) } ?: JsonNull)
        put("max_title_number", maximum?.let { JsonPrimitive(it) } ?: JsonNull)
        put("unnumbered_items", unnumbered.size)
        put("repeated_number_items", numbered.size - counts.size)
        put("missing_numbers_assumption", "仅按1到当前最高识别编号核对未出现的数字；不代表这些期曾发布、删除或收费。")
        put("missing_number_count", missing.size)
        putJsonArray("missing_number_ranges") { ranges.take(40).forEach { add(it) } }
        put("missing_ranges_truncated", ranges.size > 40)
        putJsonArray("unnumbered_examples") { unnumbered.take(6).forEach { add(it.title.take(200)) } }
        put("unnumbered_examples_truncated", unnumbered.size > 6)
        putJsonArray("duplicate_number_examples") { duplicates.entries.sortedBy { it.key }.take(8).forEach {
            add(buildJsonObject { put("number", it.key); put("count", it.value) })
        } }
        put("duplicate_examples_truncated", duplicates.size > 8)
        put("parser_diagnostics_available", feed.diagnostics != null)
        feed.diagnostics?.let {
            put("raw_rss_items", it.rawItems); put("rejected_missing_title_or_audio", it.rejectedItems)
            put("omitted_after_1000_item_limit", it.cappedItems); put("duplicate_identity_items", it.duplicateItems)
        }
        put("cause_limit", "只能证明当前目录与编号的差异；具体缺期原因需发布者说明，不能根据差值断言删集。")
    }
}
