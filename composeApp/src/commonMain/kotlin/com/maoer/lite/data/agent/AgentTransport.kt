package com.maoer.lite.data.agent

class AgentTransportException(val code: String) : Exception(code)

expect class AgentTransport() {
    suspend fun endpoint(connection: AgentConnection): String
    suspend fun probe(ssh: AgentSshConfig): String
    fun close()
}

expect fun protectAgentConnection(value: String): String
expect fun unprotectAgentConnection(value: String): String
