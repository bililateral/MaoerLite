package com.maoer.lite.data.agent

import com.maoer.lite.data.podcast.BuiltInPodcasts
import kotlin.random.Random

/** Local starter prompts, sampled only when a conversation is created. */
fun agentStarterQuestions(previous: List<String> = emptyList(), random: Random = Random.Default): List<String> {
    val sources = BuiltInPodcasts.sources
    val pools = listOf(
        sources.map { "介绍一下《${it.title}》，说说推荐理由" }.distinct(),
        sources.map { it.category }.distinct().map { "推荐几个${it}节目，并说明理由" },
        listOf("旅行", "焦虑", "成长", "阅读", "音乐", "职场", "创业", "亲密关系")
            .map { "找两期聊${it}的分集，说明推荐依据" },
    )
    return pools.map { pool ->
        pool.filterNot { it in previous }.ifEmpty { pool }.random(random)
    }.shuffled(random)
}
