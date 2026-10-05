package com.maoer.lite.data.agent

import kotlinx.coroutines.*

/** Connection validation and persistence; the session owns UI state and admission. */
internal class AgentConnectionManager(private val storage: AgentStorage, private val api: AgentApi) {
    suspend fun configure(value: AgentConnection, history: AgentHistory): Boolean {
        value.validationError()?.let { throw IllegalArgumentException(it) }
        val url = agentServiceUrl(value.url)
        value.ssh?.validate()
        if (value.ssh != null) require(url.startsWith("http://")) { "SSH 隧道内请填写 http:// 开头的服务地址。" }
        require(value.token.length >= 32 && value.token.all { it.code in 33..126 })
        val normalized = value.copy(url = url)
        val turn = history.turns.lastOrNull()
        val pending = history.deletePending || history.cancelPending != null || turn?.let { it.run?.terminal != true } == true
        val remote = api.checkConnection(normalized, if (pending) history.cancelPending ?: turn?.id ?: agentId() else agentId())
        history.requireRecoverableConnection(remote)
        try { storage.saveConnection(normalized) }
        catch (e: CancellationException) { throw e }
        catch (_: Exception) { throw IllegalStateException("连接验证成功，但手机未能保存配置，请检查设备安全存储后重试。") }
        withContext(NonCancellable + Dispatchers.IO) { runCatching { api.closeConnection() } }
        return pending
    }
}
