package com.maoer.lite.data.library

import com.maoer.lite.data.local.getDataStorePath
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okio.FileSystem
import okio.Path
import okio.Path.Companion.toPath
import okio.ByteString.Companion.encodeUtf8

class FileLibraryPersistence(
    private val directory: Path = getDataStorePath().toPath().parent!! / "listening-library-v1",
) : LibraryPersistence {
    private val fs = FileSystem.SYSTEM
    private val json = Json { ignoreUnknownKeys = true }
    private fun path(id: String) = directory / "episode-${id.encodeUtf8().sha256().hex()}.json"
    override suspend fun entries(): List<ListeningEntry> = withContext(Dispatchers.Default) {
        if (!fs.exists(directory)) emptyList() else fs.list(directory)
            .filter { it.name.startsWith("episode-") && it.name.endsWith(".json") }
            .map { path -> fs.read(path) { json.decodeFromString<ListeningEntry>(readUtf8()) } }
    }
    override suspend fun write(entry: ListeningEntry) = atomicWrite(path(entry.audio.id), json.encodeToString(entry))
    override suspend fun delete(id: String) = withContext(Dispatchers.Default) { fs.delete(path(id), mustExist = false) }
    override suspend fun bookmarks(): Set<String> = withContext(Dispatchers.Default) {
        val path = directory / "bookmarks.json"
        if (fs.exists(path)) fs.read(path) { json.decodeFromString<Set<String>>(readUtf8()) } else emptySet()
    }
    override suspend fun writeBookmarks(ids: Set<String>) = atomicWrite(directory / "bookmarks.json", json.encodeToString(ids))
    private suspend fun atomicWrite(path: Path, value: String) = withContext(Dispatchers.Default) {
        fs.createDirectories(directory)
        val temp = directory / "${path.name}.tmp"
        try {
            fs.write(temp) { writeUtf8(value) }
            fs.atomicMove(temp, path)
        } finally { fs.delete(temp, mustExist = false) }
    }
}
