package com.maoer.lite.data.agent

/** Application-facing conversation facade. Its controller remains app scoped across navigation. */
class AgentSession internal constructor(private val controller: AgentConversationController) {
    val state = controller.state
    suspend fun connection() = controller.connection()
    suspend fun sourceText(run: String, number: Int) = controller.sourceText(run, number)
    suspend fun probeSsh(value: AgentSshConfig) = controller.probeSsh(value)
    suspend fun configure(value: AgentConnection) = controller.configure(value)
    fun setPersonalizedSearch(value: Boolean) = controller.setPersonalizedSearch(value)
    fun send(question: String) = controller.send(question)
    fun reconnect(retry: Boolean = false) = controller.reconnect(retry)
    fun stop() = controller.stop()
    fun newConversation() = controller.newConversation()
}
