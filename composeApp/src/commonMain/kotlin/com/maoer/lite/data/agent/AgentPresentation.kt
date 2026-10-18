package com.maoer.lite.data.agent

/** Two stable list items per turn: question then answer, independent of text length. */
internal fun latestAgentItem(turns: Int, hasAnswer: Boolean): Int =
    ((turns - 1).coerceAtLeast(0) * 2) + if (turns > 0 && hasAnswer) 1 else 0

internal fun agentWorkLabel(run: AgentRun?): String {
    val phase = when (run?.phase) {
        "intent" -> "正在理解你的要求…"
        "model_planning" -> "正在安排查询与操作…"
        "model_analysis" -> "正在整理已取得的结果…"
        "search_rss" -> "正在查找相关分集…"
        "read_episode_description" -> "正在阅读分集说明…"
        "web_search" -> "正在搜索公开资料…"
        "read_web_page" -> "正在阅读网页资料…"
        "device_receipt" -> "正在核对手机操作结果…"
        else -> "正在处理你的请求…"
    }
    val received = run?.results.orEmpty().distinctBy { it.call_id }.size
    return phase + if (received > 0) " 已返回${received}项结果" else ""
}
