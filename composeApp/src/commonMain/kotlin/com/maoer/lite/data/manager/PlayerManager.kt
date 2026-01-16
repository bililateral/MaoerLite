package com.maoer.lite.data.manager

import com.maoer.lite.data.local.KeyValueStorage
import com.maoer.lite.data.model.Audio
import com.maoer.lite.data.repository.MaoerRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * 管理应用程序的全局音频播放状态。
 *
 * 这个单例类（通过 Koin 作用域管理）处理：
 * 1. 持有当前播放的音频和播放列表上下文。
 * 2. 管理播放状态（播放/暂停）。
 * 3. 模拟播放进度（计时器），因为我们尚未连接真实的媒体引擎。
 * 4. 将最后播放的音频及其进度持久化到磁盘，以便用户稍后恢复。
 */
class PlayerManager(
    private val repository: MaoerRepository,
    private val storage: KeyValueStorage
) {
    // 后台任务（进度计时）的作用域，在 UI 组合更改后仍然存活。
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())
    private var progressJob: Job? = null

    // 当前选中的音频曲目。
    private val _currentAudio = MutableStateFlow<Audio?>(null)
    val currentAudio = _currentAudio.asStateFlow()

    // 全局播放/暂停状态。
    private val _isPlaying = MutableStateFlow(false)
    val isPlaying = _isPlaying.asStateFlow()

    // 曲目的当前进度（0.0f 到 1.0f）。
    private val _progress = MutableStateFlow(0f)
    val progress = _progress.asStateFlow()

    // 本地播放列表上下文，以支持“下一首”和“上一首”导航。
    private var playlist: List<Audio> = emptyList()

    init {
        scope.launch {
            // 在应用启动时从 KeyValueStorage 恢复状态。
            // 这样可以确保用户能够准确地从他们离开的地方继续。
            val lastId = storage.getString("last_audio_id")
            val lastProgressStr = storage.getString("last_audio_progress")
            val audios = repository.getRecommendAudios()
            playlist = audios
            
            if (lastId != null) {
                // 尝试在当前列表中查找上次播放的音频。
                val lastAudio = audios.find { it.id == lastId }
                if (lastAudio != null) {
                    _currentAudio.value = lastAudio
                    _progress.value = lastProgressStr?.toFloatOrNull() ?: 0f
                } else if (audios.isNotEmpty()) {
                     // 如果 ID 有效但不在获取的列表中（例如，内容已删除），
                     // 则回退到第一项，以避免播放器栏为空。
                    _currentAudio.value = audios.first()
                    _progress.value = 0f
                }
            } else if (audios.isNotEmpty()) {
                // 首次启动或无历史记录：默认播放第一首曲目。
                _currentAudio.value = audios.first()
                _progress.value = 0f
            }
        }
    }

    /**
     * 更新当前的播放列表上下文。
     * 当用户与音频列表交互时（例如，点击首页中的项目）调用。
     */
    fun setPlaylist(list: List<Audio>) {
        this.playlist = list
    }

    /**
     * 开始播放特定的音频曲目。
     * 如果是新曲目，则重置进度；如果是同一曲目，则恢复播放。
     */
    fun play(audio: Audio) {
        if (_currentAudio.value?.id != audio.id) {
            // 选择了新曲目：重置状态并立即持久化 ID。
            progressJob?.cancel() // 取消旧的任务，确保新曲目有一个干净的开始
            _currentAudio.value = audio
            _progress.value = 0f
            storage.saveString("last_audio_id", audio.id)
            storage.saveString("last_audio_progress", "0.0")
        }
        resume()
    }
    
    /**
     * 如果当前处于暂停状态，则恢复播放。
     * 启动进度模拟计时器。
     */
    fun resume() {
        _isPlaying.value = true
        if (progressJob?.isActive == true) return
        startProgressTicker()
    }

    /**
     * 暂停播放。
     * 取消计时器以节省资源并持久化确切的进度。
     */
    fun pause() {
        _isPlaying.value = false
        progressJob?.cancel()
        storage.saveString("last_audio_progress", _progress.value.toString())
    }

    /**
     * 跳到当前播放列表中的下一首曲目。
     * 如果在末尾，则循环回到开头。
     */
    fun next() {
        val current = _currentAudio.value ?: return
        val index = playlist.indexOfFirst { it.id == current.id }
        if (index != -1) {
            val nextIndex = (index + 1) % playlist.size
            play(playlist[nextIndex])
        }
    }

    /**
     * 跳到上一首曲目。
     * 如果在开头，则循环到末尾。
     */
    fun previous() {
        val current = _currentAudio.value ?: return
        val index = playlist.indexOfFirst { it.id == current.id }
        if (index != -1) {
            val prevIndex = if (index - 1 < 0) playlist.size - 1 else index - 1
            play(playlist[prevIndex])
        }
    }

    /**
     * 寻道至特定位置 (0.0 - 1.0)。
     * 由 UI 滑块使用。立即持久化新位置。
     */
    fun seekTo(value: Float) {
        val newProgress = value.coerceIn(0f, 1f)
        _progress.value = newProgress
        storage.saveString("last_audio_progress", newProgress.toString())
    }

    /**
     * 模拟音频播放进度，因为我们缺乏真实的媒体播放器后端。
     * 每 100ms 更新一次进度，并每 1s 自动保存到磁盘。
     */
    private fun startProgressTicker() {
        progressJob?.cancel()
        progressJob = scope.launch {
            var tickCount = 0
            while (isActive && _isPlaying.value) {
                val current = _currentAudio.value
                if (current == null) {
                    _isPlaying.value = false
                    break
                }
                
                // 将时长 "MM:SS" 解析为秒以计算步长。
                val durationParts = current.duration.split(":")
                val totalSeconds = if (durationParts.size == 2) {
                    (durationParts[0].toIntOrNull() ?: 0) * 60 + (durationParts[1].toIntOrNull() ?: 0)
                } else {
                    300
                }

                delay(100) // update every 100ms
                val step = 0.1f / totalSeconds.coerceAtLeast(1)
                _progress.value = (_progress.value + step).coerceAtMost(1f)
                
                // 每 1 秒（10 个 tick）保存一次进度，以最大程度减少 I/O，同时保持状态相对新鲜。
                tickCount++
                if (tickCount % 10 == 0) {
                    storage.saveString("last_audio_progress", _progress.value.toString())
                }
                
                // 播放结束时自动切换到下一首
                if (_progress.value >= 1f) {
                    next()
                }
            }
        }
    }
}