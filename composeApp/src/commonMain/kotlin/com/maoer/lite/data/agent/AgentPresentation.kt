package com.maoer.lite.data.agent

import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive

/** Two stable list items per turn: question then answer, independent of text length. */
internal fun latestAgentItem(turns: Int, hasAnswer: Boolean): Int =
    ((turns - 1).coerceAtLeast(0) * 2) + if (turns > 0 && hasAnswer) 1 else 0

internal fun agentWorkLabel(run: AgentRun?): String {
    if (run?.status == "awaiting_tools") {
        val call = run.calls.singleOrNull()
        val action = when (call?.name) {
            "query_library" -> "正在读取资料库…"
            "search_catalog" -> "正在查询节目目录…"
            "get_podcast_details" -> "正在读取节目详情…"
            "list_episodes" -> "正在查询分集列表…"
            "search_podcast_content" -> "正在检索节目说明…"
            "get_playback_state" -> "正在读取播放状态…"
            "play_episode" -> "正在播放所选分集…"
            "pause_playback" -> "正在暂停播放…"
            "resume_playback" -> "正在恢复播放…"
            "previous_episode" -> "正在切换上一集…"
            "next_episode" -> "正在切换下一集…"
            "set_playback_speed" -> "正在设置播放速度…"
            "set_sleep_timer" -> when (call.arguments["minutes"]?.jsonPrimitive?.intOrNull) {
                0 -> "正在取消睡眠定时…"
                -1 -> "正在设置播完停止…"
                else -> "正在设置睡眠定时…"
            }
            else -> "正在处理手机请求…"
        }
        return action + if (run.batch_total > 1) " ${run.batch_completed + 1}/${run.batch_total}" else ""
    }
    val responding = run?.text?.isNotBlank() == true
    val phase = when (run?.phase) {
        "intent" -> "正在理解你的要求…"
        "list_recent_updates" -> "正在核对各档节目更新…"
        "model_planning" -> if (responding) "正在生成回复…" else "正在思考如何回答…"
        "model_analysis" -> if (responding) "正在生成回复…" else "正在整理已取得的结果…"
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
