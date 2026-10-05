package com.maoer.lite.data.agent

import kotlinx.serialization.json.*

internal fun agentToolError(note: String, reason: String = "tool_unavailable") = buildJsonObject {
    put("status", "error"); put("reason", reason); put("note", note)
}
