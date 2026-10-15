package com.maoer.lite.data.agent

import com.maoer.lite.data.error.errorText
import com.maoer.lite.data.error.AppFailure
import kotlinx.coroutines.*

/** Connection validation and persistence; the session owns UI state and admission. */
internal class AgentConnectionManager(private val storage: AgentStorage, private val api: AgentApi) {
    suspend fun configure(value: AgentConnection, history: AgentHistory): Boolean {
        value.validationError()?.let { throw IllegalArgumentException(it) }
        val url = agentServiceUrl(value.url)
        value.ssh?.validate()
        if (value.ssh != null) require(url.startsWith("http://")) { errorText("ssh_target_invalid") }
        require(value.token.length >= 32 && value.token.all { it.code in 33..126 })
        val normalized = value.copy(url = url)
        val turn = history.turns.lastOrNull()
        val pending = history.deletePending || history.cancelPending != null || turn?.let { it.run?.terminal != true } == true
        val remote = api.checkConnection(normalized, if (pending) history.cancelPending ?: turn?.id ?: agentId() else agentId())
        history.requireRecoverableConnection(remote)
        try { storage.saveConnection(normalized) }
        catch (e: CancellationException) { throw e }
        catch (_: Exception) { throw AppFailure("connection_save_failed") }
        withContext(NonCancellable + Dispatchers.IO) { runCatching { api.closeConnection() } }
        return pending
    }
}
