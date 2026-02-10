package com.maoer.lite.data.manager

import com.maoer.lite.data.local.KeyValueStorage
import com.maoer.lite.data.model.Audio
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlin.time.Duration.Companion.seconds
import kotlin.time.TimeSource

/**
 * 管理应用程序的全局音频播放状态（跨页面共享）。
 *
 * 这个单例类（通过 Koin 注入）处理：
 * 1. 维护当前播放曲目 [currentAudio] 与播放列表上下文（用于上一首/下一首）。
 * 2. 将“播放/暂停/切歌/拖动进度”等意图转发给平台侧的 [MediaPlayerController]。
 * 3. 将最后播放的音频 ID 与进度持久化到 DataStore，以便下次启动恢复。
 *
 * 设计取舍（为什么不在这里拉取推荐列表）：
 * - 首页已经会加载推荐列表。如果 PlayerManager 也在 init 再拉一次，会造成重复请求与潜在不一致。
 * - PlayerManager 只负责“收到列表后建立上下文”，因此这里等待 UI 调用 [setPlaylist] 注入列表。
 *
 * 恢复逻辑：
 * - init 只读取 last_audio_id/last_audio_progress 并暂存；
 * - 等到 [setPlaylist] 被调用且 restore 信息已读完后，再选择要 prepare 的音频并恢复进度；
 * - prepare/seek 可能在 Android MediaController 未就绪前发生，Android 侧会缓存命令并在连接后重放。
 */
class PlayerManager(
    private val storage: KeyValueStorage,
    private val mediaController: MediaPlayerController
) {
    // 后台任务的作用域
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    // 当前选中的音频曲目。
    private val _currentAudio = MutableStateFlow<Audio?>(null)
    val currentAudio = _currentAudio.asStateFlow()

    // 委托给 MediaPlayerController 的状态
    val isPlaying = mediaController.isPlaying
    val progress = mediaController.currentProgress

    // 本地播放列表上下文
    private var playlist: List<Audio> = emptyList()
    private var playlistIds: List<String> = emptyList()

    // 从本地读取到的“上次播放快照”。只有在 playlist 已知后才能映射到具体 Audio 对象。
    private var pendingRestoreId: String? = null
    private var pendingRestoreProgress: Float? = null
    // 只有 restore 信息读取完成后才允许挑选“默认曲目”，否则可能覆盖掉真正要恢复的 last_id。
    private var restoreInfoLoaded: Boolean = false

    init {
        scope.launch {
            // 只读取恢复信息，等待 UI 提供 playlist（避免重复拉取推荐列表与竞态丢指令）。
            pendingRestoreId = storage.getString("last_audio_id")
            pendingRestoreProgress = storage.getString("last_audio_progress")?.toFloatOrNull()?.coerceIn(0f, 1f)
            restoreInfoLoaded = true
            maybeApplyRestore()
        }

        // 监听控制器的音频ID变化，更新当前音频对象
        scope.launch {
            mediaController.currentAudioId.collect { id ->
                if (id != null) {
                    val audio = playlist.find { it.id == id }
                    if (audio != null) {
                        _currentAudio.value = audio
                        storage.saveString("last_audio_id", id)
                    }
                }
            }
        }

        // 监听进度并保存 (降低频率，例如只在暂停或特定间隔保存，这里简化为随流更新，实际 Controller 可能已经做了)
        scope.launch {
            // App 被划掉/进程被回收时不一定会触发 pause()，因此这里做一个低频持久化兜底。
            // Android 端真实播放在 Service 中，也会在 onTaskRemoved/onDestroy 额外保存一次。
            val saveInterval = 5.seconds
            var lastSaved = TimeSource.Monotonic.markNow() - saveInterval
            progress.collect { p ->
                if (!isPlaying.value) return@collect
                if (lastSaved.elapsedNow() < saveInterval) return@collect
                lastSaved = TimeSource.Monotonic.markNow()
                // 保存的是比例 0..1（而不是毫秒），恢复时由平台侧换算成 seekTo(positionMs)。
                storage.saveString("last_audio_progress", p.toString())
            }
        }
    }

    fun setPlaylist(list: List<Audio>) {
        val ids = list.map { it.id }
        if (ids == playlistIds) return

        playlistIds = ids
        this.playlist = list
        mediaController.setPlaylist(list)

        // Don't guess a track until restore info is loaded; otherwise we may lose "last played" restore.
        if (restoreInfoLoaded) maybeApplyRestore()
    }

    private fun maybeApplyRestore() {
        if (!restoreInfoLoaded) return
        if (_currentAudio.value != null) return
        if (playlist.isEmpty()) return

        val restoreId = pendingRestoreId
        val chosen = restoreId?.let { id -> playlist.find { it.id == id } } ?: playlist.first()
        _currentAudio.value = chosen

        // Prepare (no autoplay) + restore seek.
        mediaController.prepare(chosen)
        pendingRestoreProgress?.let { p -> mediaController.seekTo(p) }

        pendingRestoreId = null
        pendingRestoreProgress = null
    }

    fun play(audio: Audio) {
        // 如果是新的音频，更新当前引用并通知控制器播放
        if (_currentAudio.value?.id != audio.id) {
            _currentAudio.value = audio
            scope.launch {
                storage.saveString("last_audio_id", audio.id)
            }
        }
        mediaController.play(audio)
    }

    fun resume() {
        mediaController.resume()
    }

    fun pause() {
        mediaController.pause()
        scope.launch {
            storage.saveString("last_audio_progress", progress.value.toString())
        }
    }

    fun next() {
        val current = _currentAudio.value ?: return
        val index = playlist.indexOfFirst { it.id == current.id }
        if (index != -1 && playlist.isNotEmpty()) {
            val nextIndex = (index + 1) % playlist.size
            play(playlist[nextIndex])
        }
    }

    fun previous() {
        val current = _currentAudio.value ?: return
        val index = playlist.indexOfFirst { it.id == current.id }
        if (index != -1 && playlist.isNotEmpty()) {
            val prevIndex = if (index - 1 < 0) playlist.size - 1 else index - 1
            play(playlist[prevIndex])
        }
    }

    fun seekTo(value: Float) {
        mediaController.seekTo(value)
        scope.launch {
             storage.saveString("last_audio_progress", value.toString())
        }
    }
}
