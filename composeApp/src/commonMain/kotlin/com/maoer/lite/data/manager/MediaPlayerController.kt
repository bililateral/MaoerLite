package com.maoer.lite.data.manager

import com.maoer.lite.data.model.Audio
import kotlinx.coroutines.flow.StateFlow

interface MediaPlayerController {
    val playbackSpeed: StateFlow<Float>
    val sleepTimer: StateFlow<SleepTimerState>
    fun setPlaybackSpeed(speed: Float)
    /** null cancels; -1 pauses when the current episode naturally finishes. */
    fun setSleepTimer(minutes: Int?)
    val positionMs: StateFlow<Long>
    val durationMs: StateFlow<Long>
    val buffering: StateFlow<Boolean>
    val playbackError: StateFlow<String?>
    fun seekToMs(position: Long)
    val isPlaying: StateFlow<Boolean>
    /**
     * 当前播放进度（0.0..1.0）。
     *
     * 约定：
     * - 这是“比例”而不是毫秒，便于跨平台统一展示与持久化。
     * - 当 duration 未知时（例如刚 prepare 还没 READY），平台实现可以先保持旧值或直接返回 0，
     *   并在 duration 可用后更新。
     */
    val currentProgress: StateFlow<Float>
    val currentAudioId: StateFlow<String?>

    /**
     * 设置播放列表上下文（用于自动续播、通知栏上一首/下一首等）。
     *
     * 各平台可以按需实现：例如 Android 将其映射为 ExoPlayer/MediaController 的队列；
     * iOS/桌面未实现真实播放时可以忽略。
     *
     * 重要：如果列表不变，平台实现应尽量避免重建队列（setMediaItems），否则切歌会产生额外卡顿。
     */
    fun setPlaylist(playlist: List<Audio>)

    /**
     * 预加载音频但不开始播放（用于恢复上次播放或提前准备）。
     */
    fun prepare(audio: Audio)

    fun play(audio: Audio)
    fun pause()
    fun resume()
    fun seekTo(position: Float)
    fun release()
}
