package com.maoer.lite.data.agent

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okio.FileSystem
import okio.Path.Companion.toPath

expect fun agentStoragePath(): String
expect fun agentPublishedMillis(value: String): Long?

/** Private, excluded from Android backup. Atomic writes fail before device effects. */
class AgentStorage {
    private val root = agentStoragePath().toPath()
    private val fs = FileSystem.SYSTEM
    private val lock = Mutex()
    private val json = Json { ignoreUnknownKeys = true }
    private suspend fun read(name: String): String? = withContext(Dispatchers.Default) {
        lock.withLock { val p = root / name; if (fs.exists(p)) fs.read(p) { readUtf8() } else null }
    }
    private suspend fun write(name: String, value: String) = withContext(Dispatchers.Default) {
        lock.withLock {
            fs.createDirectories(root)
            val tmp = root / "$name.tmp"
            try { fs.write(tmp) { writeUtf8(value) }; fs.atomicMove(tmp, root / name) }
            finally { fs.delete(tmp, mustExist = false) }
        }
    }
    suspend fun connection(): AgentConnection {
        val text = read("connection.json") ?: return AgentConnection()
        val connection = json.decodeFromString<AgentConnection>(if (text.startsWith("enc:v1:")) unprotectAgentConnection(text) else text)
        if (!text.startsWith("enc:v1:")) saveConnection(connection)
        return connection
    }
    suspend fun saveConnection(value: AgentConnection) = write("connection.json", protectAgentConnection(json.encodeToString(value)))
    suspend fun history() = read("history.json")?.let { json.decodeFromString<AgentHistory>(it) } ?: AgentHistory()
    suspend fun saveHistory(value: AgentHistory) = write("history.json", json.encodeToString(value))
    suspend fun execution(key: String) = read("execution-$key.json")?.let { json.decodeFromString<AgentExecution>(it) }
    suspend fun saveExecution(key: String, value: AgentExecution) = write("execution-$key.json", json.encodeToString(value))

    /** Called only after remote deletion acknowledgement, before replacing history. */
    suspend fun clearExecutions(conversationId: String) = withContext(Dispatchers.Default) {
        lock.withLock {
            if (!fs.exists(root)) return@withLock
            fs.list(root).filter { Regex("execution-[a-f0-9]{64}\\.json").matches(it.name) }.forEach { path ->
                val value = json.decodeFromString<AgentExecution>(fs.read(path) { readUtf8() })
                // Legacy receipts predate ownership; this app has only one live conversation.
                // They belong to this or already closed conversations, never a future task.
                if (value.conversationId.isEmpty() || value.conversationId == conversationId) fs.delete(path)
            }
        }
    }
}
