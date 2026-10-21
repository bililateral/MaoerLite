package com.maoer.lite.data.agent

import com.maoer.lite.data.error.errorText
import com.maoer.lite.data.error.AppFailure
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
    if (runCatching { agentServiceUrl(url) }.isFailure) return errorText("connection_url_invalid")
    if (token.length < 32 || token.any { it.code !in 33..126 }) return errorText("connection_token_invalid")
    ssh?.let {
        if (it.host.isBlank() || it.host.length > 253 || it.host.any { c -> !c.isLetterOrDigit() && c !in ".-" })
            return errorText("ssh_host_invalid")
        if (it.port !in 1..65535) return errorText("ssh_port_invalid")
        if (it.username.isBlank() || it.username.length > 64 || it.username.any { c -> c.isWhitespace() || c.isISOControl() })
            return errorText("ssh_user_invalid")
        if (it.password.isEmpty() || it.password.length > 1024) return errorText("ssh_password_missing")
        if (!Regex("SHA256:[A-Za-z0-9+/]{43}").matches(it.fingerprint)) return errorText("ssh_fingerprint_missing")
        if (!agentServiceUrl(url).startsWith("http://")) return errorText("ssh_target_invalid")
    }
    return null
}
@Serializable data class AgentCall(val id: String, val name: String, val arguments: JsonObject)
@Serializable data class AgentReceipt(val call_id: String, val result: JsonObject, val recovered: Boolean = false)
@Serializable data class AgentRun(
    val id: String, val conversation_id: String, val status: String,
    val text: String = "", val calls: List<AgentCall> = emptyList(),
    val results: List<AgentReceipt> = emptyList(), val claimed: List<String> = emptyList(),
    val error: String = "", val retry_after: Int = 0, val version: Long = 0,
    val batch_completed: Int = 0, val batch_total: Int = 0,
    val summary_turns: Int = 0, val summary_pending: Int = 0,
    val sources: List<JsonObject> = emptyList(), val search_hint: String = "",
    val phase: String = "", val error_detail: JsonObject = JsonObject(emptyMap()),
) { val terminal get() = status in listOf("completed", "cancelled", "failed") }

@Serializable data class AgentSourceText(val text: String = "", val complete: Boolean = false, val source_url: String = "")
@Serializable data class AgentTurn(val id: String, val question: String, val run: AgentRun? = null)
@Serializable data class AgentHistory(val conversationId: String = agentId(), val turns: List<AgentTurn> = emptyList(), val cancelPending: String? = null, val deletePending: Boolean = false,
                                     val starterQuestions: List<String> = emptyList())

fun AgentHistory.requireRecoverableConnection(remote: AgentRun?) {
    val turn = turns.lastOrNull()
    val pending = deletePending || cancelPending != null || turn?.let { it.run?.terminal != true } == true
    if (!pending) return
    if (remote != null && (remote.id != (cancelPending ?: turn?.id) || remote.conversation_id != conversationId))
        throw AppFailure("connection_identity_mismatch")
    // Without any saved run, follow() has not dispatched a device tool. Retain the request ID.
    if (remote == null && (turn?.run != null || deletePending || cancelPending != null))
        throw AppFailure("connection_task_missing")
}
@Serializable data class AgentNewRun(val id: String, val conversation_id: String, val message: String,
                                   val capabilities: List<String> = emptyList())
@Serializable data class AgentResults(val results: List<AgentReceipt>)
@Serializable data class AgentExecution(val fingerprint: String, val result: JsonObject? = null,
                                       val conversationId: String = "", val runId: String = "",
                                       val processInstance: String = "")

fun agentId(): String {
    val hex = List(32) { "0123456789abcdef"[Random.nextInt(16)] }.joinToString("")
    return "${hex.take(8)}-${hex.substring(8,12)}-${hex.substring(12,16)}-${hex.substring(16,20)}-${hex.substring(20)}"
}
