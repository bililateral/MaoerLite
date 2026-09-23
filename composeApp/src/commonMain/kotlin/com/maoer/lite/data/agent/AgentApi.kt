package com.maoer.lite.data.agent

import io.ktor.client.HttpClient
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.request.*
import io.ktor.client.statement.*
import io.ktor.http.*
import io.ktor.utils.io.readUTF8Line
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*

class AgentApiException(val code: String, val status: Int) : Exception(code)

class AgentApi(private val storage: AgentStorage) {
    private val transport = AgentTransport()
    fun closeConnection() = transport.close()
    suspend fun probeSsh(ssh: AgentSshConfig) = transport.probe(ssh)
    private val json = Json { ignoreUnknownKeys = true }
    // Separate from RSS client: an idle model can take much longer than a feed request.
    private val client = HttpClient { install(HttpTimeout) {
        requestTimeoutMillis = 240_000; connectTimeoutMillis = 10_000; socketTimeoutMillis = 35_000
    } }
    private suspend fun request(path: String, value: String? = null, post: Boolean = false): AgentRun {
        val connection = storage.connection()
        require(connection.token.isNotBlank()) { "connection_missing" }
        val response = client.request(transport.endpoint(connection) + path) {
            method = if (post) HttpMethod.Post else HttpMethod.Get
            header(HttpHeaders.Authorization, "Bearer ${connection.token}")
            if (value != null) { contentType(ContentType.Application.Json); setBody(value) }
        }
        val body = response.bodyAsText()
        if (response.status.value !in 200..299) fail(body, response.status.value)
        return json.decodeFromString(body)
    }
    private fun fail(body: String, status: Int): Nothing {
        val code = runCatching { json.parseToJsonElement(body).jsonObject["error"]?.jsonObject?.get("code")?.jsonPrimitive?.content }.getOrNull()
        throw AgentApiException(code ?: "connection_error", status)
    }
    suspend fun create(value: AgentNewRun) = request("/v1/agent/runs", json.encodeToString(value), true)
    suspend fun get(id: String) = request("/v1/agent/runs/$id")
    suspend fun cancel(id: String) = request("/v1/agent/runs/$id/cancel", post = true)
    suspend fun retry(id: String) = request("/v1/agent/runs/$id/retry", post = true)
    suspend fun claim(id: String, call: String) = request("/v1/agent/runs/$id/claim/${call.encodeURLPathPart()}", post = true)
    suspend fun results(id: String, values: List<AgentReceipt>) = request("/v1/agent/runs/$id/results", json.encodeToString(AgentResults(values)), true)
    suspend fun deleteConversation(id: String) {
        val connection = storage.connection()
        require(connection.token.isNotBlank()) { "connection_missing" }
        val response = client.request(transport.endpoint(connection) + "/v1/agent/conversations/${id.encodeURLPathPart()}") {
            method = HttpMethod.Delete
            header(HttpHeaders.Authorization, "Bearer ${connection.token}")
        }
        val body = response.bodyAsText()
        if (response.status.value !in 200..299) fail(body, response.status.value)
        val result = json.parseToJsonElement(body).jsonObject
        check(result["deleted"]?.jsonPrimitive?.booleanOrNull == true && result["conversation_id"]?.jsonPrimitive?.content == id)
    }
    suspend fun events(id: String, update: suspend (AgentRun) -> Unit) {
        val connection = storage.connection()
        client.prepareGet(transport.endpoint(connection) + "/v1/agent/runs/$id/events") {
            header(HttpHeaders.Authorization, "Bearer ${connection.token}")
        }.execute { response ->
            if (response.status.value != 200) fail(response.bodyAsText(), response.status.value)
            val channel = response.bodyAsChannel()
            while (true) {
                val line = channel.readUTF8Line(1_048_576) ?: break
                if (line.startsWith("data: ")) update(json.decodeFromString<AgentRun>(line.removePrefix("data: ")))
            }
        }
    }
}
