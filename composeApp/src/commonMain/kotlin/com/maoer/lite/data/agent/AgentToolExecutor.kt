package com.maoer.lite.data.agent

import com.maoer.lite.data.error.errorText
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import okio.ByteString.Companion.encodeUtf8
import com.maoer.lite.data.error.*

/** Persist the execution boundary once; business handlers never manage receipts. */
internal class AgentToolExecutor(
    private val storage: AgentStorage,
    private val claim: suspend (String, String) -> Unit,
    private val perform: suspend (AgentCall) -> JsonObject,
    private val snapshot: (String, String) -> JsonObject,
) {
    private fun state(status: String = "ok", note: String = "") = snapshot(status, note)
    private fun error(note: String, reason: String = "tool_unavailable") = agentToolError(note, reason)
    suspend fun execute(run: String, call: AgentCall, conversationId: String = ""): AgentReceipt {
        val key = "$run:${call.id}".encodeUtf8().sha256().hex()
        val fingerprint = "${call.name}:${call.arguments}".encodeUtf8().sha256().hex()
        val old = storage.execution(key)
        if (old != null) {
            require(old.fingerprint == fingerprint) { errorText("tool_arguments_changed") }
            val result = old.result ?: state("unknown", errorText("operation_interrupted"))
            storage.saveExecution(key, old.copy(result = result, conversationId = conversationId, runId = run))
            return AgentReceipt(call.id, result)
        }
        // Claim has no device side effect and is safe to repeat if its response
        // is lost. Do not persist an execution marker until authorization succeeds.
        claim(run, call.id)
        val execution = AgentExecution(fingerprint, conversationId = conversationId, runId = run)
        storage.saveExecution(key, execution)
        var result: JsonObject
        try {
            result = withContext(Dispatchers.Main) { perform(call) }
        } catch (cancelled: CancellationException) {
            withContext(NonCancellable) {
                storage.saveExecution(key, execution.copy(result = state("unknown", errorText("operation_cancelled"))))
            }
            throw cancelled
        } catch (e: Exception) {
            result = if (call.name in setOf("list_episodes", "get_podcast_details", "search_podcast_content"))
                error(failureText(e, rssFailureCode(e)), rssFailureCode(e))
            else if (call.name == "search_catalog") error(errorText("catalog_query_failed"), "catalog_query_failed")
            else if (call.name == "query_library") error(errorText("library_read_failed"), "library_read_failed")
            else state("unknown", errorText("operation_unconfirmed")).let { snapshot ->
                buildJsonObject { snapshot.forEach { (key, value) -> put(key, value) }; put("reason", "operation_unconfirmed") }
            }
        }
        storage.saveExecution(key, execution.copy(result = result))
        return AgentReceipt(call.id, result)
    }

    /** Recover only already claimed, locally recorded effects; never execute a tool. */
    suspend fun unsubmittedReceipts(run: AgentRun): List<AgentReceipt> {
        val submitted = run.results.map { it.call_id }.toSet()
        return run.claimed.filterNot { it in submitted }.mapNotNull { id ->
            val key = "${run.id}:$id".encodeUtf8().sha256().hex()
            val old = storage.execution(key) ?: return@mapNotNull null
            val result = old.result ?: withContext(Dispatchers.Main) {
                state("unknown", errorText("operation_recovered_unknown"))
            }
            if (old.result == null) storage.saveExecution(key, old.copy(result = result))
            AgentReceipt(id, result)
        }
    }
}
