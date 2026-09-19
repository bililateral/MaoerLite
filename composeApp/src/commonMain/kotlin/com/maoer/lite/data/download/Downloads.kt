package com.maoer.lite.data.download

import com.maoer.lite.data.model.Audio
import com.maoer.lite.data.podcast.PodcastSource
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.serialization.Serializable

@Serializable
enum class DownloadStatus { QUEUED, DOWNLOADING, WAITING, COMPLETE, FAILED }

@Serializable
data class DownloadItem(val audio: Audio, val requestId: Long, val status: DownloadStatus = DownloadStatus.QUEUED,
    val bytes: Long = 0, val total: Long = -1, val localUri: String? = null, val message: String? = null)

object DownloadPolicy {
    fun allowed(audio: Audio, sources: List<PodcastSource>): Boolean =
        sources.any { it.id == audio.podcastId && it.downloadAllowed } &&
            (audio.audioUrl.startsWith("https://") || audio.audioUrl.startsWith("http://"))
}

interface Downloads {
    val items: StateFlow<List<DownloadItem>>
    val error: StateFlow<String?>
    fun enqueue(audio: Audio)
    fun remove(id: String)
    fun clearError()
    fun localUri(id: String): String?
    suspend fun awaitReady()
}

/** iOS remains outside the delivered scope; preserve dependency graph without simulating downloads. */
class UnavailableDownloads : Downloads {
    override val items = MutableStateFlow<List<DownloadItem>>(emptyList())
    override val error = MutableStateFlow<String?>(null)
    override fun enqueue(audio: Audio) { error.value = "当前平台尚不支持下载" }
    override fun remove(id: String) = Unit
    override fun clearError() { error.value = null }
    override fun localUri(id: String): String? = null
    override suspend fun awaitReady() = Unit
}

fun downloadLabel(item: DownloadItem): String = when (item.status) {
    DownloadStatus.QUEUED -> "等待下载"
    DownloadStatus.DOWNLOADING -> if (item.total > 0) "下载中 ${(item.bytes * 100 / item.total).coerceIn(0, 100)}%" else "下载中"
    DownloadStatus.WAITING -> "等待网络"
    DownloadStatus.COMPLETE -> "已下载 · ${item.bytes / 1024 / 1024} MB"
    DownloadStatus.FAILED -> item.message ?: "下载失败，可重试"
}
