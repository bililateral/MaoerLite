package com.maoer.lite.data.agent

import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import okio.ByteString.Companion.encodeUtf8

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
            require(old.fingerprint == fingerprint) { "工具 ID 对应的参数发生变化" }
            val result = old.result ?: state("unknown", "上次执行中断，已查询当前状态；为避免重复操作，不重新执行。")
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
                storage.saveExecution(key, execution.copy(result = state("unknown", "任务已停止，操作可能已生效；未重复执行。")))
            }
            throw cancelled
        } catch (_: Exception) {
            result = if (call.name in setOf("list_episodes", "get_podcast_details", "search_podcast_content"))
                error("RSS资料读取或解析未完成，不表示节目不存在或分集越界；请稍后重查。", "rss_load_failed")
            else if (call.name == "search_catalog") error("节目目录查询未完成，请核对查询条件。", "catalog_query_failed")
            else state("unknown", "本次操作未能确认完成；以下为实际播放器状态，不要重复执行控制操作。").let { snapshot ->
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
                state("unknown", "操作执行中断，以下为当前状态；未重复执行原操作。")
            }
            if (old.result == null) storage.saveExecution(key, old.copy(result = result))
            AgentReceipt(id, result)
        }
    }
}
