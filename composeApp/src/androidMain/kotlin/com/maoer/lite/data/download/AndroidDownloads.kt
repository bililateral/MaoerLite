package com.maoer.lite.data.download

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Environment
import com.maoer.lite.MaoerApplication
import com.maoer.lite.data.local.KeyValueStorage
import com.maoer.lite.data.model.Audio
import com.maoer.lite.data.podcast.BuiltInPodcasts
import com.maoer.lite.data.podcast.PodcastSource
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okio.ByteString.Companion.encodeUtf8
import java.io.File

/** Android owns transfers across UI/process lifetimes; common code owns source policy and display state. */
class AndroidDownloads(private val storage: KeyValueStorage,
    private val context: Context = MaoerApplication.instance,
    private val sources: List<PodcastSource> = BuiltInPodcasts.sources,
) : Downloads {
    private val manager = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val mutex = Mutex()
    private val loaded = CompletableDeferred<Unit>()
    private val json = Json { ignoreUnknownKeys = true }
    private val _items = MutableStateFlow<List<DownloadItem>>(emptyList())
    override val items = _items.asStateFlow()
    private val _error = MutableStateFlow<String?>(null)
    override val error = _error.asStateFlow()
    init { scope.launch {
        try {
            _items.value = storage.getString(KEY)?.let { json.decodeFromString<List<DownloadItem>>(it) }.orEmpty()
            refresh()
            loaded.complete(Unit)
            while (isActive) {
                delay(1500)
                try {
                    refresh()
                    if (_error.value == "下载状态暂不可用，正在重试") _error.value = null
                } catch (e: CancellationException) { throw e }
                catch (_: Exception) { _error.value = "下载状态暂不可用，正在重试" }
            }
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) {
            _error.value = "下载记录读取失败，请重新打开应用"
            loaded.completeExceptionally(e)
        }
    } }
    override suspend fun awaitReady() = loaded.await()
    fun close() { scope.cancel() }
    override fun clearError() { _error.value = null }
    override fun localUri(id: String): String? = _items.value.find { it.audio.id == id && it.status == DownloadStatus.COMPLETE }
        ?.localUri?.takeIf { uri -> Uri.parse(uri).let { it.scheme == "file" && File(it.path.orEmpty()).isFile } }

    override fun enqueue(audio: Audio) { action {
        check(DownloadPolicy.allowed(audio, sources)) { "此节目暂不提供离线下载" }
        awaitReady()
        mutex.withLock {
            val old = _items.value.find { it.audio.id == audio.id }
            if (old != null && old.status != DownloadStatus.FAILED) return@withLock
            old?.let { manager.remove(it.requestId) }
            val name = "${audio.id.encodeUtf8().sha256().hex()}-${System.nanoTime()}.audio"
            val request = DownloadManager.Request(Uri.parse(audio.audioUrl))
                .setTitle(audio.title).setDescription(audio.author)
                .setAllowedOverRoaming(false)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalFilesDir(context, Environment.DIRECTORY_MUSIC, name)
            val id = manager.enqueue(request)
            try { save(_items.value.filterNot { it.audio.id == audio.id } + DownloadItem(audio, id)) }
            catch (e: Exception) { manager.remove(id); throw e }
        }
    } }
    override fun remove(id: String) { action {
        awaitReady()
        mutex.withLock {
            val item = _items.value.find { it.audio.id == id } ?: return@withLock
            manager.remove(item.requestId)
            save(_items.value.filterNot { it.audio.id == id })
        }
    } }
    private suspend fun refresh() = mutex.withLock {
        val updated = _items.value.map { item ->
            manager.query(DownloadManager.Query().setFilterById(item.requestId)).use { cursor ->
                if (!cursor.moveToFirst()) item.copy(status = DownloadStatus.FAILED, localUri = null, message = "文件已移除，可重新下载")
                else {
                    fun long(column: String) = cursor.getLong(cursor.getColumnIndexOrThrow(column))
                    var status = when (long(DownloadManager.COLUMN_STATUS).toInt()) {
                        DownloadManager.STATUS_SUCCESSFUL -> DownloadStatus.COMPLETE
                        DownloadManager.STATUS_FAILED -> DownloadStatus.FAILED
                        DownloadManager.STATUS_RUNNING -> DownloadStatus.DOWNLOADING
                        DownloadManager.STATUS_PAUSED -> DownloadStatus.WAITING
                        else -> DownloadStatus.QUEUED
                    }
                    val uri = cursor.getString(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_LOCAL_URI))
                    if (status == DownloadStatus.COMPLETE && (uri == null || !File(Uri.parse(uri).path.orEmpty()).isFile)) status = DownloadStatus.FAILED
                    item.copy(status = status, bytes = long(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR).coerceAtLeast(0),
                        total = long(DownloadManager.COLUMN_TOTAL_SIZE_BYTES), localUri = uri.takeIf { status == DownloadStatus.COMPLETE },
                        message = if (status == DownloadStatus.FAILED) "下载失败或文件缺失，可重试（${long(DownloadManager.COLUMN_REASON)}）" else null)
                }
            }
        }
        if (updated != _items.value) save(updated)
    }
    private suspend fun save(items: List<DownloadItem>) {
        storage.saveString(KEY, json.encodeToString(items))
        _items.value = items
    }
    private fun action(block: suspend () -> Unit) { scope.launch {
        try { _error.value = null; block() } catch (e: CancellationException) { throw e }
        catch (e: Exception) { _error.value = e.message ?: "下载操作失败，请检查网络与剩余空间" }
    } }
    companion object { const val KEY = "offline_downloads_v1" }
}
