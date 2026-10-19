package com.maoer.lite.data.agent

import com.maoer.lite.data.error.errorText
import io.ktor.utils.io.errors.IOException
import com.maoer.lite.data.error.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*

data class AgentUiState(val history: AgentHistory = AgentHistory(), val ready: Boolean = false,
                        val connected: Boolean = false, val working: Boolean = false, val notice: String = "",
                        val personalizedSearch: Boolean = true)

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
            mutable.value = AgentUiState(history, ready = true, connected = storage.connection().token.isNotBlank(),
                                        personalizedSearch = storage.personalizedSearch())
        }
        catch (e: CancellationException) { throw e }
        catch (e: Exception) { mutable.value = mutable.value.copy(ready = false, notice = agentFailureMessage(e)) }
    } }
    suspend fun connection() = storage.connection()
    suspend fun sourceText(run: String, number: Int) = api.sourceText(run, number)
    fun setPersonalizedSearch(value: Boolean) {
        scope.launch {
            try {
                storage.savePersonalizedSearch(value)
                mutable.value = mutable.value.copy(personalizedSearch = value)
                if (!value) save(mutable.value.history.copy(turns = mutable.value.history.turns.map {
                    it.copy(run = it.run?.copy(search_hint = ""))
                }))
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { mutable.value = mutable.value.copy(notice = errorText("hint_save_failed")) }
        }
    }
    private fun capabilities() = listOf("episode_selection_v2", "research_v3", "library_read_v1") +
        if (mutable.value.personalizedSearch) listOf("search_hints") else emptyList()
    suspend fun probeSsh(value: AgentSshConfig) = api.probeSsh(value)
    suspend fun configure(value: AgentConnection) = withContext(Dispatchers.Main) {
        if (configuring || mutable.value.working) throw AppFailure("configuration_busy")
        if (!mutable.value.ready) throw AppFailure("local_records_loading")
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
        val visible = if (mutable.value.personalizedSearch) run else run.copy(search_hint = "")
        save(h.copy(turns = h.turns.map { if (it.id == run.id) it.copy(run = visible) else it }))
    }
    private fun failureNotice(reason: String) {
        val prefix = if (mutable.value.history.deletePending) errorText("delete_pending_notice") else ""
        mutable.value = mutable.value.copy(notice = prefix + reason)
    }
    private fun work(block: suspend () -> Unit) {
        if (configuring || job?.isActive == true) return
        mutable.value = mutable.value.copy(working = true, notice = "")
        job = scope.launch {
            try { block() }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { failureNotice(agentFailureMessage(e)) }
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
            follow(api.create(AgentNewRun(turn.id, old.conversationId, turn.question, capabilities())))
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
            api.create(AgentNewRun(turn.id, h.conversationId, turn.question, capabilities()))
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
                if (!run.terminal && run.status != "awaiting_tools") throw AppFailure("service_stream_ended")
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
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { mutable.value = mutable.value.copy(notice = errorText("stop_save_failed")) }
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
