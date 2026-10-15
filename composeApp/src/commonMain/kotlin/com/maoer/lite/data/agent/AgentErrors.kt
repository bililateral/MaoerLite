package com.maoer.lite.data.agent

import com.maoer.lite.data.error.*
import kotlinx.serialization.json.*

fun agentError(code: String): String = errorText(code)

fun agentError(run: AgentRun): String {
    val kind = (run.error_detail["timeout_kind"] as? JsonPrimitive)?.contentOrNull
    val code = if (run.error == "upstream_timeout" && kind in setOf("connect", "write", "pool", "response_headers", "first_data", "stream_read", "total")) "model_timeout_$kind" else run.error
    val message = errorText(code,
        "seconds" to (run.error_detail["total_limit_seconds"] as? JsonPrimitive)?.intOrNull,
        "status" to (run.error_detail["http_status"] as? JsonPrimitive)?.intOrNull)
    val stages = setOf("intent", "summary", "model_planning", "model_analysis", "device_receipt", "web_search", "read_web_page", "search_rss", "read_episode_description")
    return if (run.phase in stages) errorText("model_error_context", "stage" to errorText("phase_${run.phase}"), "message" to message) else message
}

fun agentFailureMessage(error: Throwable): String = when (error) {
    is AgentApiException -> errorText(error.code, "status" to error.status)
    is AgentTransportException -> errorText(error.code)
    is AppFailure -> failureText(error, error.code)
    else -> errorText(serviceFailureCode(error))
}
