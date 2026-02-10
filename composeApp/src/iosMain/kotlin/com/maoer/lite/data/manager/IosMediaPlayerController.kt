package com.maoer.lite.data.manager

import com.maoer.lite.data.model.Audio
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * iOS 端播放器控制器的存根实现（仅用于 Demo 编译通过）。
 *
 * 当前项目重点在 Android 侧接入 Media3/ExoPlayer；
 * iOS 若要达标，需要引入 AVPlayer + 后台音频会话 + 控制中心/锁屏控制等。
 */
class IosMediaPlayerController : MediaPlayerController {
    private val _isPlaying = MutableStateFlow(false)
    override val isPlaying: StateFlow<Boolean> = _isPlaying.asStateFlow()

    private val _currentProgress = MutableStateFlow(0f)
    override val currentProgress: StateFlow<Float> = _currentProgress.asStateFlow()

    private val _currentAudioId = MutableStateFlow<String?>(null)
    override val currentAudioId: StateFlow<String?> = _currentAudioId.asStateFlow()

    override fun setPlaylist(playlist: List<Audio>) {
        // no-op (stub)
    }

    override fun prepare(audio: Audio) {
        _currentAudioId.value = audio.id
    }

    override fun play(audio: Audio) {
        _isPlaying.value = true
        _currentAudioId.value = audio.id
    }

    override fun pause() {
        _isPlaying.value = false
    }

    override fun resume() {
        _isPlaying.value = true
    }

    override fun seekTo(position: Float) {
        _currentProgress.value = position
    }

    override fun release() {
        // no-op
    }
}
