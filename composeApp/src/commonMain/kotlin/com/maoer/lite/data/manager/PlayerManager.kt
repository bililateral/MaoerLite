package com.maoer.lite.data.manager

import com.maoer.lite.data.local.KeyValueStorage
import com.maoer.lite.data.model.Audio
import com.maoer.lite.data.library.ListeningLibrary
import com.maoer.lite.data.library.ListeningPolicy
import com.maoer.lite.data.podcast.PodcastRepository
import com.maoer.lite.data.download.Downloads
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*

/** UI selects a queue; actual playing/position/error state always comes from Media3. */
class PlayerManager(private val storage: KeyValueStorage, private val mediaController: MediaPlayerController,
    private val library: ListeningLibrary, private val podcasts: PodcastRepository, private val downloads: Downloads) {
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private val snapshotStore = PlaybackSnapshotStore(storage)
    private val _currentAudio = MutableStateFlow<Audio?>(null)
    val currentAudio = _currentAudio.asStateFlow()
    val isPlaying = mediaController.isPlaying
    val playbackAudioId = mediaController.currentAudioId
    val progress = mediaController.currentProgress
    val positionMs = mediaController.positionMs
    val durationMs = mediaController.durationMs
    val buffering = mediaController.buffering
    val playbackError = mediaController.playbackError
    val playbackSpeed = mediaController.playbackSpeed
    val sleepTimer = mediaController.sleepTimer
    private val _queue = MutableStateFlow<List<Audio>>(emptyList())
    val queue = _queue.asStateFlow()
    private var playlist: List<Audio> = emptyList()
    private var userSelected = false
    private var selectionJob: Job? = null
    private val _libraryError = MutableStateFlow<String?>(null)
    val libraryError = _libraryError.asStateFlow()

    init {
        scope.launch {
            optional { downloads.awaitReady() }
            val saved = optional { snapshotStore.load() }
            if (!userSelected && saved != null) {
                val audio = saved.queue.find { it.id == saved.currentId }?.let { enrich(it) }
                if (audio != null) {
                    if (userSelected) return@launch
                    // Older installs only have the last queue snapshot. Preserve that progress once.
                    // Timestamp 1 means historical time unknown; don't pretend restoration is a new listen.
                    optional {
                        if (saved.positionMs > 0 && library.entry(audio.id) == null)
                            library.record(audio, saved.positionMs, 0, 1)
                    }
                    playlist = saved.queue.map { if (it.id == audio.id) audio else it }
                    _queue.value = playlist
                    mediaController.setPlaylist(playlist)
                    _currentAudio.value = audio
                    // A reconnect to an already-playing service must not reset its position.
                    if (mediaController.currentAudioId.value == null) {
                        mediaController.setPlaybackSpeed(PlaybackOptions.validSpeed(saved.speed))
                        mediaController.prepare(audio)
                        mediaController.seekToMs(saved.positionMs)
                    }
                }
            }
        }
        scope.launch {
            mediaController.currentAudioId.collectLatest { id ->
                playlist.find { it.id == id }?.let {
                    _currentAudio.value = it
                    val enriched = enrich(it)
                    if (mediaController.currentAudioId.value == id) _currentAudio.value = enriched
                }
            }
        }
        // Service also persists this format independently of the UI lifecycle.
    }

    fun setPlaylist(list: List<Audio>) {
        userSelected = true
        if (list == playlist) return
        playlist = list
        _queue.value = list
        mediaController.setPlaylist(list)
    }
    fun play(audio: Audio) {
        userSelected = true
        selectionJob?.cancel()
        selectionJob = scope.launch {
            try {
                _libraryError.value = null
                val enriched = enrich(audio)
                optional { library.rememberAudio(enriched) }
                val same = mediaController.currentAudioId.value == audio.id
                val entry = optional { library.entry(audio.id) }
                val start = ListeningPolicy.resumePosition(entry)
                if (entry?.completed == true) optional { library.restart(enriched) }
                optional { downloads.awaitReady() }
                if (playlist.none { it.id == audio.id }) setPlaylist(listOf(enriched))
                _currentAudio.value = enriched
                mediaController.play(enriched)
                if (!same || entry?.completed == true) mediaController.seekToMs(start)
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { _libraryError.value = "无法保存续听记录，请检查存储空间后重试" }
        }
    }
    private suspend fun enrich(audio: Audio): Audio {
        val saved = optional { library.entry(audio.id)?.audio }
        val cached = if (audio.podcastId.isNotBlank()) optional { podcasts.cached(audio.podcastId) } else null
        return cached?.episodes?.find { it.id == audio.id }?.asAudio(cached) ?: saved ?: audio
    }
    private suspend fun <T> optional(block: suspend () -> T): T? = try { block() }
        catch (e: CancellationException) { throw e }
        catch (_: Exception) { _libraryError.value = "本地记录暂不可用，播放可继续；原记录已保留"; null }
    fun resume() {
        val audio = currentAudio.value
        if (audio != null && library.entries.value.any { it.audio.id == audio.id && it.completed }) play(audio)
        else mediaController.resume()
    }
    fun pause() { selectionJob?.cancel(); mediaController.pause() }
    fun next() {
        val index = playlist.indexOfFirst { it.id == _currentAudio.value?.id }
        if (index >= 0) play(playlist[QueueNavigation.nextIndex(index, playlist.size)])
    }
    fun previous() {
        val index = playlist.indexOfFirst { it.id == _currentAudio.value?.id }
        if (index >= 0) play(playlist[QueueNavigation.previousIndex(index, playlist.size)])
    }
    fun seekBy(deltaMs: Long) {
        mediaController.seekToMs((positionMs.value + deltaMs).coerceIn(0L, durationMs.value.coerceAtLeast(0)))
    }
    fun seekTo(value: Float) = mediaController.seekTo(value)
    fun setPlaybackSpeed(speed: Float) = mediaController.setPlaybackSpeed(PlaybackOptions.validSpeed(speed))
    fun setSleepTimer(minutes: Int?) {
        require(minutes == null || minutes == -1 || minutes in 1..180)
        mediaController.setSleepTimer(minutes)
    }
    companion object { const val SNAPSHOT_KEY = PlaybackSnapshotStore.KEY }
}
