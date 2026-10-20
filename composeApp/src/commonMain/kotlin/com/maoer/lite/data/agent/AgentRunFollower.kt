package com.maoer.lite.data.agent

import com.maoer.lite.data.error.AppFailure
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.isActive

/** Follows one operation in its caller's scope; owns no independent job or UI state. */
internal class AgentRunFollower(private val api: AgentApi, private val tools: AgentTools) {
    suspend fun follow(initial: AgentRun, update: suspend (AgentRun) -> Unit) {
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
}
