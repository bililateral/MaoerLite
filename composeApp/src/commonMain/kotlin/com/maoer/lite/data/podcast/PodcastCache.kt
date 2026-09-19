package com.maoer.lite.data.podcast

import com.maoer.lite.data.local.getDataStorePath
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okio.FileSystem
import okio.Path
import okio.Path.Companion.toPath
import okio.ByteString.Companion.encodeUtf8

interface PodcastCache {
    suspend fun read(id: String): CachedFeed?
    suspend fun write(id: String, value: CachedFeed)
}

/** Independent, atomic per-feed cache. No full catalog rewritten on a feed refresh. */
class FilePodcastCache : PodcastCache {
    private val json = Json { ignoreUnknownKeys = true }
    private val directory: Path get() = getDataStorePath().toPath().parent!! / "podcast-cache-v1"
    private fun path(id: String) = directory / "${id.encodeUtf8().sha256().hex()}.json"
    override suspend fun read(id: String): CachedFeed? = withContext(Dispatchers.Default) {
        val path = path(id)
        if (!FileSystem.SYSTEM.exists(path)) return@withContext null
        try {
            val cached = FileSystem.SYSTEM.read(path) { json.decodeFromString<CachedFeed>(readUtf8()) }
            cached.takeIf { it.feed.sourceId == id }
        } catch (_: Exception) { null } // Corrupt cache is refetched, never treated as real feed data.
    }
    override suspend fun write(id: String, value: CachedFeed) = withContext(Dispatchers.Default) {
        FileSystem.SYSTEM.createDirectories(directory)
        val path = path(id)
        val temporary = directory / "${path.name}.tmp"
        try {
            FileSystem.SYSTEM.write(temporary) { writeUtf8(json.encodeToString(value)) }
            FileSystem.SYSTEM.atomicMove(temporary, path)
        } finally {
            FileSystem.SYSTEM.delete(temporary, mustExist = false)
        }
    }
}
