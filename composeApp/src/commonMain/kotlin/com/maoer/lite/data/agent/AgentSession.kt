package com.maoer.lite.data.agent

import io.ktor.utils.io.errors.IOException
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*

data class AgentUiState(val history: AgentHistory = AgentHistory(), val ready: Boolean = false,
                        val connected: Boolean = false, val working: Boolean = false, val notice: String = "")

/** App-scoped session survives navigation; explicit stop cancels further dispatch. */
class AgentSession(private val storage: AgentStorage, private val api: AgentApi, private val tools: AgentTools) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val mutable = MutableStateFlow(AgentUiState())
    val state = mutable.asStateFlow()
    private var job: Job? = null
    private var configuring = false
    private val connections = AgentConnectionManager(storage, api)
    init { scope.launch {
        try {
            val saved = storage.history()
            val history = if (saved.starterQuestions.isEmpty()) {
                saved.copy(starterQuestions = agentStarterQuestions()).also { storage.saveHistory(it) }
            } else saved
            mutable.value = AgentUiState(history, ready = true, connected = storage.connection().token.isNotBlank())
        }
        catch (_: Exception) { mutable.value = mutable.value.copy(ready = false, notice = "本地助手记录无法读取，原文件已保留，请先修复本地记录。") }
    } }
    suspend fun connection() = storage.connection()
    suspend fun probeSsh(value: AgentSshConfig) = api.probeSsh(value)
    suspend fun configure(value: AgentConnection) = withContext(Dispatchers.Main) {
        check(!configuring && !mutable.value.working) { "助手正在处理请求，请停止后再修改连接。" }
        check(mutable.value.ready) { "本地助手记录尚未加载，请稍后重试。" }
        configuring = true
        try {
            val pending = connections.configure(value, mutable.value.history)
            mutable.value = mutable.value.copy(connected = true, notice = if (pending)
                "连接已验证并保存；点击恢复连接继续原请求。" else "连接已验证并保存")
        } finally { configuring = false }
    }
    private suspend fun save(history: AgentHistory) {
        storage.saveHistory(history)
        mutable.value = mutable.value.copy(history = history)
    }
    private suspend fun update(run: AgentRun) {
        val h = mutable.value.history
        save(h.copy(turns = h.turns.map { if (it.id == run.id) it.copy(run = run) else it }))
    }
    private fun failureNotice(reason: String) {
        val prefix = if (mutable.value.history.deletePending) "旧对话删除尚未确认，请重试删除。" else ""
        mutable.value = mutable.value.copy(notice = prefix + reason)
    }
    private fun work(block: suspend () -> Unit) {
        if (configuring || job?.isActive == true) return
        mutable.value = mutable.value.copy(working = true, notice = "")
        job = scope.launch {
            try { block() }
            catch (e: CancellationException) { throw e }
            catch (e: AgentApiException) { failureNotice(agentError(e.code)) }
            catch (e: AgentTransportException) { failureNotice(agentError(e.code)) }
            catch (_: IOException) { failureNotice("连接已中断，请检查网络或 VPN 后点击恢复连接；SSH 模式会重新建立隧道并继续原任务。") }
            catch (_: Exception) { failureNotice(if (mutable.value.history.deletePending) "请检查连接或本地存储后重试。" else "连接已中断，恢复连接会继续当前任务，不会重新执行已记录操作。") }
            finally { api.closeConnection(); mutable.value = mutable.value.copy(working = false) }
        }
    }
    fun send(question: String) {
        if (!mutable.value.ready || !mutable.value.connected || question.isBlank() || question.length > 2000) return
        val old = mutable.value.history
        if (old.deletePending || old.cancelPending != null || old.turns.lastOrNull()?.let { it.run?.terminal != true } == true) return
        work {
            val turn = AgentTurn(agentId(), question.trim())
            save(old.copy(turns = (old.turns + turn).takeLast(30)))
            follow(api.create(AgentNewRun(turn.id, old.conversationId, turn.question, listOf("episode_selection_v2"))))
        }
    }
    fun reconnect(retry: Boolean = false) = work {
        val h = mutable.value.history
        if (h.deletePending) { finishNewConversation(h); return@work }
        if (h.cancelPending != null) {
            try {
                var cancelled = api.cancel(h.cancelPending)
                update(cancelled)
                if (cancelled.status == "cancelled") {
                    for (receipt in tools.unsubmittedReceipts(cancelled)) {
                        cancelled = api.results(cancelled.id, listOf(receipt))
                        update(cancelled)
                    }
                }
            }
            catch (e: AgentApiException) {
                if (e.status != 404) throw e
                update(AgentRun(h.cancelPending, h.conversationId, "cancelled"))
            }
            save(mutable.value.history.copy(cancelPending = null)); return@work
        }
        val turn = h.turns.lastOrNull() ?: return@work
        val run = try { api.get(turn.id) } catch (e: AgentApiException) {
            if (e.status != 404) throw e
            api.create(AgentNewRun(turn.id, h.conversationId, turn.question, listOf("episode_selection_v2")))
        }
        follow(if (retry && run.status == "failed") api.retry(run.id) else run)
    }
    private suspend fun follow(initial: AgentRun) {
        var run = initial
        while (currentCoroutineContext().isActive) {
            update(run)
            if (run.terminal) return
            if (run.status == "awaiting_tools") {
                // Recheck server status before any dispatch. Claim is the execution boundary.
                run = api.get(run.id)
                if (run.status != "awaiting_tools") continue
                val receipts = run.calls.map { call -> tools.execute(run.id, call, run.conversation_id) }
                currentCoroutineContext().ensureActive()
                run = api.results(run.id, receipts)
            } else {
                api.events(run.id) { value -> run = value; update(value) }
                if (!run.terminal && run.status != "awaiting_tools") throw IllegalStateException("stream ended")
            }
        }
    }
    fun stop() {
        if (mutable.value.history.deletePending) return
        val id = mutable.value.history.turns.lastOrNull()?.id ?: return
        val activeJob = job
        activeJob?.cancel()
        scope.launch {
            try {
                activeJob?.join()
                save(mutable.value.history.copy(cancelPending = id))
                reconnect()
            } catch (_: Exception) { mutable.value = mutable.value.copy(notice = "停止状态保存失败，当前客户端已停止派发操作。") }
        }
    }
    fun newConversation() {
        if (!mutable.value.ready || mutable.value.working || mutable.value.history.cancelPending != null || mutable.value.history.deletePending) return
        if (mutable.value.history.turns.lastOrNull()?.let { it.run?.terminal != true } == true) return
        work {
            val old = mutable.value.history
            if (old.turns.isNotEmpty()) {
                save(old.copy(deletePending = true))
                finishNewConversation(mutable.value.history)
            } else save(AgentHistory(starterQuestions = agentStarterQuestions(old.starterQuestions)))
        }
    }
    private suspend fun finishNewConversation(old: AgentHistory) {
        api.deleteConversation(old.conversationId)
        storage.clearExecutions(old.conversationId)
        // If the response or this local write fails, keep the durable deletion
        // intent. Reconnect repeats DELETE rather than recreating an old run.
        save(AgentHistory(starterQuestions = agentStarterQuestions(old.starterQuestions)))
    }
}
