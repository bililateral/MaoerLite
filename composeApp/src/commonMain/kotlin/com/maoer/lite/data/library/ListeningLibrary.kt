package com.maoer.lite.data.library

import com.maoer.lite.data.model.Audio
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable

@Serializable
data class ListeningEntry(
    val audio: Audio,
    val positionMs: Long = 0,
    val durationMs: Long = 0,
    val lastPlayedAt: Long = 0,
    val favorite: Boolean = false,
    val completed: Boolean = false,
)

interface LibraryPersistence {
    suspend fun entries(): List<ListeningEntry>
    suspend fun write(entry: ListeningEntry)
    suspend fun delete(id: String)
    suspend fun bookmarks(): Set<String>
    suspend fun writeBookmarks(ids: Set<String>)
}

object ListeningPolicy {
    fun resumePosition(entry: ListeningEntry?): Long =
        if (entry == null || entry.completed) 0 else entry.positionMs.coerceAtLeast(0)

    fun progress(old: ListeningEntry, position: Long, duration: Long, playedAt: Long, ended: Boolean, restarted: Boolean = false): ListeningEntry {
        val total = duration.takeIf { it > 0 } ?: old.durationMs
        val bounded = position.coerceAtLeast(0).let { if (total > 0) it.coerceAtMost(total) else it }
        // Completion is explicit or an actual end transition, never inferred from seeking near the end.
        return old.copy(positionMs = if (ended && total > 0) total else bounded,
            durationMs = total, lastPlayedAt = maxOf(old.lastPlayedAt, playedAt),
            completed = ended || (old.completed && !restarted))
    }
}

/** Shared rules; each episode is written atomically without rewriting the whole listening history. */
class ListeningLibrary(
    private val persistence: LibraryPersistence,
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Main),
) {
    private val mutex = Mutex()
    private val loaded = CompletableDeferred<Unit>()
    private val _entries = MutableStateFlow<List<ListeningEntry>>(emptyList())
    val entries = _entries.asStateFlow()
    private val _bookmarks = MutableStateFlow<Set<String>>(emptySet())
    val bookmarks = _bookmarks.asStateFlow()
    private val _error = MutableStateFlow<String?>(null)
    val error = _error.asStateFlow()
    private val _ready = MutableStateFlow(false)
    val ready = _ready.asStateFlow()

    init { scope.launch {
        try {
            _entries.value = persistence.entries().sortedByDescending { it.lastPlayedAt }
            _bookmarks.value = persistence.bookmarks()
            _ready.value = true
            loaded.complete(Unit)
        } catch (e: Exception) {
            _error.value = "收听记录读取失败，请重新打开应用；原文件已保留"
            loaded.completeExceptionally(e)
        }
    } }

    suspend fun awaitReady() = loaded.await()
    suspend fun entry(id: String): ListeningEntry? { awaitReady(); return _entries.value.find { it.audio.id == id } }
    fun clearError() { _error.value = null }
    fun reportSaveFailure() { _error.value = "收听记录保存失败，请检查剩余空间后重试" }

    fun toggleFavorite(audio: Audio) = action { mutate(audio) { it.copy(favorite = !it.favorite) } }
    fun toggleBookmark(id: String) = action {
        awaitReady()
        mutex.withLock {
            val ids = _bookmarks.value.let { if (id in it) it - id else it + id }
            persistence.writeBookmarks(ids)
            _bookmarks.value = ids
        }
    }
    fun setCompleted(audio: Audio, completed: Boolean) = action {
        mutate(audio) { it.copy(completed = completed,
            positionMs = if (completed && it.durationMs > 0) it.durationMs else 0) }
    }
    fun removeHistory(audio: Audio) = action {
        awaitReady()
        mutex.withLock {
            val old = _entries.value.find { it.audio.id == audio.id } ?: return@withLock
            if (old.favorite) {
                val cleared = old.copy(positionMs = 0, durationMs = 0, lastPlayedAt = 0, completed = false)
                persistence.write(cleared)
                _entries.value = _entries.value.map { if (it.audio.id == audio.id) cleared else it }
            } else {
                persistence.delete(audio.id)
                _entries.value = _entries.value.filterNot { it.audio.id == audio.id }
            }
        }
    }
    suspend fun rememberAudio(audio: Audio) = mutate(audio) { it }
    suspend fun restart(audio: Audio) = mutate(audio) { it.copy(positionMs = 0, completed = false) }
    suspend fun record(audio: Audio, position: Long, duration: Long, at: Long, ended: Boolean = false, restarted: Boolean = false) =
        mutate(audio) { ListeningPolicy.progress(it, position, duration, at, ended, restarted) }

    private suspend fun mutate(audio: Audio, transform: (ListeningEntry) -> ListeningEntry) {
        awaitReady()
        mutex.withLock {
            val previous = _entries.value.find { it.audio.id == audio.id }
            // MediaSession carries only a short summary. Keep the richer saved metadata.
            val metadata = if (previous != null && previous.audio.description.length > audio.description.length)
                audio.copy(description = previous.audio.description, duration = previous.audio.duration) else audio
            val entry = transform((previous ?: ListeningEntry(metadata)).copy(audio = metadata))
            persistence.write(entry)
            _entries.value = (_entries.value.filterNot { it.audio.id == audio.id } + entry).sortedByDescending { it.lastPlayedAt }
        }
    }
    private fun action(block: suspend () -> Unit) = scope.launch {
        try { block() } catch (e: CancellationException) { throw e }
        catch (_: Exception) { _error.value = "收听记录保存失败，请检查剩余空间后重试" }
    }
}
