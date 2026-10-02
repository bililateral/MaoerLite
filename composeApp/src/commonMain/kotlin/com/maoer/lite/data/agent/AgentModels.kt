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

@Serializable data class AgentSshConfig(
    val host: String = "", val port: Int = 22, val username: String = "",
    val password: String = "", val fingerprint: String = "",
) {
    override fun toString() = "AgentSshConfig(credentials=redacted)"
    fun validate(requireCredentials: Boolean = true) {
        require(host.isNotBlank() && host.length <= 253 && host.all { it.isLetterOrDigit() || it in ".-" })
        require(port in 1..65535 && username.isNotBlank() && username.length <= 64 && username.none { it.isWhitespace() || it.isISOControl() })
        if (requireCredentials) {
            require(password.isNotEmpty() && password.length <= 1024)
            require(Regex("SHA256:[A-Za-z0-9+/]{43}").matches(fingerprint))
        }
    }
}
@Serializable data class AgentConnection(val url: String = "http://127.0.0.1:8787", val token: String = "", val ssh: AgentSshConfig? = null) {
    override fun toString() = "AgentConnection(credentials=redacted)"
    fun sameServer(other: AgentConnection): Boolean = agentServiceUrl(url) == agentServiceUrl(other.url) &&
        ssh?.host == other.ssh?.host && ssh?.port == other.ssh?.port
}

/** Only fixed messages: never interpolate credential values into validation errors. */
fun AgentConnection.validationError(): String? {
    if (runCatching { agentServiceUrl(url) }.isFailure) return "服务地址格式不正确，例如 http://127.0.0.1:8787。"
    if (token.length < 32 || token.any { it.code !in 33..126 }) return "服务访问令牌格式不正确，请复制 agent-server.token 中的完整一行。"
    ssh?.let {
        if (it.host.isBlank() || it.host.length > 253 || it.host.any { c -> !c.isLetterOrDigit() && c !in ".-" })
            return "SSH 服务器只填 IP 或域名，不要填写 http:// 或端口。"
        if (it.port !in 1..65535) return "SSH 端口应为 1–65535 的整数，通常填 22。"
        if (it.username.isBlank() || it.username.length > 64 || it.username.any { c -> c.isWhitespace() || c.isISOControl() })
            return "请填写有效的 SSH 用户名。"
        if (it.password.isEmpty() || it.password.length > 1024) return "请填写 SSH 密码。"
        if (!Regex("SHA256:[A-Za-z0-9+/]{43}").matches(it.fingerprint)) return "请先读取服务器指纹，核对后点击信任。"
        if (!agentServiceUrl(url).startsWith("http://")) return "SSH 隧道内请填写 http:// 开头的服务地址。"
    }
    return null
}
@Serializable data class AgentCall(val id: String, val name: String, val arguments: JsonObject)
@Serializable data class AgentReceipt(val call_id: String, val result: JsonObject)
@Serializable data class AgentRun(
    val id: String, val conversation_id: String, val status: String,
    val text: String = "", val calls: List<AgentCall> = emptyList(),
    val results: List<AgentReceipt> = emptyList(), val claimed: List<String> = emptyList(),
    val error: String = "", val retry_after: Int = 0, val version: Long = 0,
    val batch_completed: Int = 0, val batch_total: Int = 0,
    val summary_turns: Int = 0, val summary_pending: Int = 0,
) { val terminal get() = status in listOf("completed", "cancelled", "failed") }
@Serializable data class AgentTurn(val id: String, val question: String, val run: AgentRun? = null)
@Serializable data class AgentHistory(val conversationId: String = agentId(), val turns: List<AgentTurn> = emptyList(), val cancelPending: String? = null, val deletePending: Boolean = false,
                                     val starterQuestions: List<String> = emptyList())

fun AgentHistory.requireRecoverableConnection(remote: AgentRun?) {
    val turn = turns.lastOrNull()
    val pending = deletePending || cancelPending != null || turn?.let { it.run?.terminal != true } == true
    if (!pending) return
    check(remote == null || (remote.id == (cancelPending ?: turn?.id) && remote.conversation_id == conversationId)) {
        "新连接返回的任务身份不一致，未保存；请检查服务器地址。"
    }
    // Without any saved run, follow() has not dispatched a device tool. Retain the request ID.
    check(remote != null || (turn?.run == null && !deletePending && cancelPending == null)) {
        "该服务找不到原任务，未保存；请连接原服务器完成任务，避免重复操作。"
    }
}
@Serializable data class AgentNewRun(val id: String, val conversation_id: String, val message: String,
                                   val capabilities: List<String> = emptyList())
@Serializable data class AgentResults(val results: List<AgentReceipt>)
@Serializable data class AgentExecution(val fingerprint: String, val result: JsonObject? = null,
                                       val conversationId: String = "", val runId: String = "")

fun agentId(): String {
    val hex = List(32) { "0123456789abcdef"[Random.nextInt(16)] }.joinToString("")
    return "${hex.take(8)}-${hex.substring(8,12)}-${hex.substring(12,16)}-${hex.substring(16,20)}-${hex.substring(20)}"
}

fun agentError(code: String): String = when (code) {
    "ssh_host_key_changed" -> "服务器身份与已确认指纹不一致，连接已拒绝；请核对服务器后再更新指纹"
    "ssh_auth_failed" -> "SSH 用户名或密码错误，请在连接设置中修正"
    "ssh_connection_failed" -> "SSH 连接失败，请检查服务器地址、端口和内网或 VPN，随后恢复连接"
    "ssh_unsupported" -> "当前平台暂不支持 SSH 隧道"
    "upstream_overloaded" -> "模型当前繁忙"
    "upstream_account_rate_limit", "local_rate_limit", "local_busy" -> "请求暂时受限"
    "upstream_timeout" -> "模型响应超时，可重试当前步骤"
    "upstream_quota_exhausted" -> "模型账户余额或额度不足，请补充额度后重试"
    "upstream_auth_failed" -> "模型服务认证失败，请检查服务端 API Key 或访问权限"
    "upstream_account_arrears", "upstream_account_restricted" -> "模型账户或额度受限，请检查服务端"
    "unauthorized" -> "连接令牌无效，请重新设置"
    "server_restarted" -> "服务已重启，请恢复当前任务"
    "tool_round_limit" -> "本次任务步骤较多，请缩小请求范围"
    "task_not_retryable" -> "当前任务无法继续，请新建对话"
    "unverified_podcast_id" -> "节目身份尚未核实，请重试当前步骤重新查询"
    "unverified_episode_id" -> "分集身份尚未核实，请重试当前步骤重新查询"
    "invalid_tool_call" -> "助手返回的工具调用格式无效，可重试当前步骤"
    "invalid_agent_response" -> "助手任务处理失败，可重试当前步骤"
    "episode_refresh_failed" -> "本轮分集查询未成功，尚未开始新的播放，请稍后重试"
    "conversation_busy" -> "当前对话还有未结束的任务"
    "conversation_deleted" -> "旧对话已删除，请新建对话"
    "conversation_delete_failed" -> "服务端删除未完成，请重试删除"
    else -> "请求未完成，请检查连接后恢复"
}
