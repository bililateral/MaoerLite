package com.maoer.lite.data.agent

// Android is the delivered platform. Never silently downgrade an SSH connection.
actual class AgentTransport actual constructor() {
    actual suspend fun endpoint(connection: AgentConnection): String {
        if (connection.ssh != null) throw AgentTransportException("ssh_unsupported")
        return agentServiceUrl(connection.url)
    }
    actual suspend fun probe(ssh: AgentSshConfig): String = throw AgentTransportException("ssh_unsupported")
    actual fun close() = Unit
}
actual fun protectAgentConnection(value: String): String = value
actual fun unprotectAgentConnection(value: String): String = throw AgentTransportException("ssh_unsupported")
