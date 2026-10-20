package com.maoer.lite.data.agent

/** Keeps existing on-disk history and pending-intent formats. The controller is the writer. */
internal class AgentConversationRecords(private val storage: AgentStorage) {
    suspend fun history() = storage.history()
    suspend fun save(value: AgentHistory) = storage.saveHistory(value)
}
