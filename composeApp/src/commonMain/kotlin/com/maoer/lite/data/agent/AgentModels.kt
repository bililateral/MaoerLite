package com.maoer.lite.data.agent

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlin.random.Random
import io.ktor.http.Url

/** Plain HTTP is limited to loopback/RFC1918; remote public endpoints require HTTPS. */
fun agentServiceUrl(value: String): String {
    val input = value.trim().trimEnd('/')
    require(input.isNotEmpty() && input.none { it.isWhitespace() })
    val url = Url(input)
    require(url.user == null && url.password == null && url.encodedQuery.isEmpty() && url.fragment.isEmpty())
    require(url.encodedPath.isEmpty() || url.encodedPath == "/")
    val segments = url.host.split('.')
    val parts = segments.mapNotNull { it.toIntOrNull() }
    val privateIp = segments.size == 4 && segments.all { it.isNotEmpty() && it.all(Char::isDigit) } && parts.size == 4 && parts.all { it in 0..255 } && (
        parts[0] == 127 || parts[0] == 10 || (parts[0] == 192 && parts[1] == 168) ||
            (parts[0] == 172 && parts[1] in 16..31))
    require(url.host.isNotBlank() && url.port in 1..65535)
    require(url.protocol.name == "https" || (url.protocol.name == "http" && privateIp))
    return url.toString().trimEnd('/')
}

@Serializable data class AgentConnection(val url: String = "http://127.0.0.1:8787", val token: String = "")
@Serializable data class AgentCall(val id: String, val name: String, val arguments: JsonObject)
@Serializable data class AgentReceipt(val call_id: String, val result: JsonObject)
@Serializable data class AgentRun(
    val id: String, val conversation_id: String, val status: String,
    val text: String = "", val calls: List<AgentCall> = emptyList(),
    val results: List<AgentReceipt> = emptyList(), val claimed: List<String> = emptyList(),
    val error: String = "", val retry_after: Int = 0, val version: Long = 0,
) { val terminal get() = status in listOf("completed", "cancelled", "failed") }
@Serializable data class AgentTurn(val id: String, val question: String, val run: AgentRun? = null)
@Serializable data class AgentHistory(val conversationId: String = agentId(), val turns: List<AgentTurn> = emptyList(), val cancelPending: String? = null, val deletePending: Boolean = false)
@Serializable data class AgentNewRun(val id: String, val conversation_id: String, val message: String)
@Serializable data class AgentResults(val results: List<AgentReceipt>)
@Serializable data class AgentExecution(val fingerprint: String, val result: JsonObject? = null)

fun agentId(): String {
    val hex = List(32) { "0123456789abcdef"[Random.nextInt(16)] }.joinToString("")
    return "${hex.take(8)}-${hex.substring(8,12)}-${hex.substring(12,16)}-${hex.substring(16,20)}-${hex.substring(20)}"
}

fun agentError(code: String): String = when (code) {
    "upstream_overloaded" -> "模型当前繁忙"
    "upstream_account_rate_limit", "local_rate_limit", "local_busy" -> "请求暂时受限"
    "upstream_timeout" -> "模型响应超时，可重试当前步骤"
    "upstream_quota_exhausted", "upstream_account_arrears", "upstream_account_restricted" -> "模型账户或额度受限，请检查服务端"
    "unauthorized" -> "连接令牌无效，请重新设置"
    "server_restarted" -> "服务已重启，请恢复当前任务"
    "tool_round_limit" -> "本次任务步骤较多，请缩小请求范围"
    "task_not_retryable" -> "当前任务无法继续，请新建对话"
    "unverified_podcast_id", "unverified_episode_id", "invalid_agent_response", "invalid_tool_call" -> "助手未返回有效操作，可重试当前步骤"
    "conversation_busy" -> "当前对话还有未结束的任务"
    "conversation_deleted" -> "旧对话已删除，请新建对话"
    "conversation_delete_failed" -> "服务端删除未完成，请重试删除"
    else -> "请求未完成，请检查连接后恢复"
}
