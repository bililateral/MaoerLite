package com.maoer.lite.data.manager

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.ListenableFuture
import com.google.common.util.concurrent.MoreExecutors
import com.maoer.lite.MaoerApplication
import com.maoer.lite.data.model.Audio
import com.maoer.lite.service.MaoerPlaybackService
import kotlinx.coroutines.Job
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.concurrent.ExecutionException

/**
 * Android 端的“播放器控制器”实现（KMP 的 actual 侧）。
 *
 * 设计目标：
 * 1. 通过 Media3 的 [MediaController] 连接到后台 [MaoerPlaybackService]（Service 内持有 ExoPlayer）。
 * 2. 给 commonMain 的 [PlayerManager] 暴露纯 Kotlin 的状态流（isPlaying / progress / currentAudioId）。
 * 3. 解决两个现实问题：
 *    - Controller 连接是异步的：UI/PlayerManager 可能在 controller 就绪前就调用 play/prepare/seek；
 *      因此这里会缓存“待执行命令”，连接成功后自动重放，避免偶发恢复失败。
 *    - MediaController 没有提供“进度实时流”：这里使用轮询读取 currentPosition/duration 并更新进度。
 *
 * 注意：
 * - progress 用 0.0..1.0 的比例表示（与 [MediaPlayerController.currentProgress] 定义一致）。
 * - 播放列表切歌时尽量避免反复 setMediaItems + prepare：列表不变时优先 seekTo(index, 0)。
 */
class AndroidMediaPlayerController : MediaPlayerController {

    private val context: Context = MaoerApplication.instance
    private var mediaControllerFuture: ListenableFuture<MediaController>? = null
    private var mediaController: MediaController? = null

    private val _isPlaying = MutableStateFlow(false)
    override val isPlaying: StateFlow<Boolean> = _isPlaying.asStateFlow()

    private val _currentProgress = MutableStateFlow(0f)
    override val currentProgress: StateFlow<Float> = _currentProgress.asStateFlow()

    private val _currentAudioId = MutableStateFlow<String?>(null)
    override val currentAudioId: StateFlow<String?> = _currentAudioId.asStateFlow()

    private val scope = MainScope()
    private var progressJob: Job? = null

    // 播放列表上下文：用于 setMediaItems 构建队列，从而实现自动续播/通知栏上下首。
    private var playlist: List<Audio> = emptyList()
    private var playlistIds: List<String> = emptyList()
    private var playlistMediaItems: List<MediaItem> = emptyList()
    private var playlistHash: Int = 0
    private var queueHash: Int = 0

    // If seek is requested before duration becomes available, apply it once READY.
    private var pendingSeekFraction: Float? = null
    // Controller 还没连上时缓存的命令（解决“恢复/点击太快导致指令丢失”的竞态）
    private var pendingPrepareAudio: Audio? = null
    private var pendingPlayAudio: Audio? = null

    init {
        val sessionToken = SessionToken(context, ComponentName(context, MaoerPlaybackService::class.java))
        mediaControllerFuture = MediaController.Builder(context, sessionToken).buildAsync()
        mediaControllerFuture?.addListener({
            try {
                mediaController = mediaControllerFuture?.get()
                setupControllerListener()
                applyPendingCommands()
            } catch (e: ExecutionException) {
                e.printStackTrace()
            } catch (e: InterruptedException) {
                e.printStackTrace()
            }
        }, MoreExecutors.directExecutor())

        // 启动一个循环来更新进度（因为 MediaController 没有实时的进度回调流）
        startProgressUpdater()
    }

    override fun setPlaylist(playlist: List<Audio>) {
        this.playlist = playlist
        playlistIds = playlist.map { it.id }
        playlistHash = playlistIds.hashCode()
        playlistMediaItems = playlist.map { a ->
            val metadata = MediaMetadata.Builder()
                .setTitle(a.title)
                .setArtist(a.author)
                .setArtworkUri(Uri.parse(a.coverUrl))
                .build()
            MediaItem.Builder()
                .setMediaId(a.id)
                .setUri(a.audioUrl)
                .setMediaMetadata(metadata)
                .build()
        }
        // Force next play/prepare to (re)apply the queue if needed.
        queueHash = 0
    }

    private fun setupControllerListener() {
        val controller = mediaController ?: return
        
        // 初始化状态
        _isPlaying.value = controller.isPlaying
        _currentAudioId.value = controller.currentMediaItem?.mediaId
        
        controller.addListener(object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                _isPlaying.value = isPlaying
            }

            override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
                _currentAudioId.value = mediaItem?.mediaId
                maybeApplyPendingSeek(controller)
            }
            
            override fun onPlaybackStateChanged(playbackState: Int) {
                if (playbackState == Player.STATE_READY) {
                    maybeApplyPendingSeek(controller)
                }
                if (playbackState == Player.STATE_ENDED) {
                     // 可以在这里处理自动播放下一首，但 PlayerManager 也可以通过进度 >= 1f 来判断
                }
            }
        })

        maybeApplyPendingSeek(controller)
    }

    override fun play(audio: Audio) {
        // Ensure the session service is started (not only bound).
        ensureServiceStarted()

        val controller = mediaController
        if (controller == null) {
            // UI/PlayerManager 可能在 controller 还没连上时就调用 play，这里先缓存，等连接完成重放。
            pendingPlayAudio = audio
            return
        }

        // 如果 ID 相同，只需 resume
        if (controller.currentMediaItem?.mediaId == audio.id) {
            if (!controller.isPlaying) {
                controller.play()
            }
            return
        }

        // 播放列表不变时不要反复 setMediaItems + prepare：只切换 index 并播放。
        val indexInPlaylist = playlistIds.indexOf(audio.id)
        if (playlistMediaItems.isNotEmpty() && indexInPlaylist >= 0) {
            val didSetQueue = ensureQueueAndSeek(controller, indexInPlaylist)
            if (didSetQueue) controller.prepare()
        } else {
            // 回退：仅播放单曲（无队列，则不会自动续播下一首）
            val metadata = MediaMetadata.Builder()
                .setTitle(audio.title)
                .setArtist(audio.author)
                .setArtworkUri(Uri.parse(audio.coverUrl))
                .build()
            val item = MediaItem.Builder()
                .setMediaId(audio.id)
                .setUri(audio.audioUrl)
                .setMediaMetadata(metadata)
                .build()
            controller.setMediaItem(item)
            controller.prepare()
        }

        controller.play()
    }

    override fun prepare(audio: Audio) {
        ensureServiceStarted()

        val controller = mediaController
        if (controller == null) {
            // 同 play：controller 未就绪时先缓存，避免恢复逻辑丢失。
            pendingPrepareAudio = audio
            return
        }

        if (controller.currentMediaItem?.mediaId == audio.id) {
            return
        }

        val indexInPlaylist = playlistIds.indexOf(audio.id)
        if (playlistMediaItems.isNotEmpty() && indexInPlaylist >= 0) {
            val didSetQueue = ensureQueueAndSeek(controller, indexInPlaylist)
            if (didSetQueue) controller.prepare()
        } else {
            val metadata = MediaMetadata.Builder()
                .setTitle(audio.title)
                .setArtist(audio.author)
                .setArtworkUri(Uri.parse(audio.coverUrl))
                .build()
            val item = MediaItem.Builder()
                .setMediaId(audio.id)
                .setUri(audio.audioUrl)
                .setMediaMetadata(metadata)
                .build()
            controller.setMediaItem(item)
            controller.prepare()
        }
    }

    override fun pause() {
        mediaController?.pause()
    }

    override fun resume() {
        mediaController?.play()
    }

    override fun seekTo(position: Float) {
        val p = position.coerceIn(0f, 1f)
        // 立即更新 UI（尤其是暂停状态下拖动 Slider），然后再异步让播放器跳转。
        _currentProgress.value = p

        val controller = mediaController
        if (controller == null) {
            pendingSeekFraction = p
            return
        }
        val duration = controller.duration
        if (duration == C.TIME_UNSET || duration <= 0L) {
            // duration 尚未可用（刚 prepare 还没 READY），先缓存，等 READY 时再真正 seek。
            pendingSeekFraction = p
            return
        }
        pendingSeekFraction = null
        controller.seekTo((p * duration).toLong())
    }

    override fun release() {
        progressJob?.cancel()
        scope.cancel()
        mediaControllerFuture?.let { MediaController.releaseFuture(it) }
        mediaControllerFuture = null
        mediaController = null
    }

    private fun startProgressUpdater() {
        progressJob = scope.launch {
            while (isActive) {
                val controller = mediaController
                if (controller != null) {
                    val total = controller.duration
                    if (total != C.TIME_UNSET && total > 0L) {
                        val current = controller.currentPosition.coerceIn(0L, total)
                        _currentProgress.value = (current.toFloat() / total.toFloat()).coerceIn(0f, 1f)
                    }
                }
                delay(200) // 每 200ms 轮询一次 UI 进度
            }
        }
    }

    private fun ensureQueueAndSeek(controller: MediaController, index: Int): Boolean {
        // If queue has not been applied for the current playlist, set it once.
        if (queueHash != playlistHash || controller.mediaItemCount == 0) {
            controller.setMediaItems(playlistMediaItems, index, /* startPositionMs */ 0L)
            queueHash = playlistHash
            return true
        } else {
            // Fast switch without rebuilding the whole queue.
            controller.seekTo(index, /* positionMs */ 0L)
            return false
        }
    }

    private fun maybeApplyPendingSeek(controller: MediaController) {
        val fraction = pendingSeekFraction ?: return
        val duration = controller.duration
        if (duration == C.TIME_UNSET || duration <= 0L) return

        pendingSeekFraction = null
        controller.seekTo((fraction.coerceIn(0f, 1f) * duration).toLong())
    }

    private fun ensureServiceStarted() {
        // Use startService (not startForegroundService) to avoid the 5s foreground requirement when we only prepare().
        context.startService(Intent(context, MaoerPlaybackService::class.java))
    }

    private fun applyPendingCommands() {
        val controller = mediaController ?: return

        pendingPrepareAudio?.let { audio ->
            pendingPrepareAudio = null
            // Safe: controller is ready now, prepare will execute immediately.
            prepare(audio)
        }

        pendingPlayAudio?.let { audio ->
            pendingPlayAudio = null
            play(audio)
        }

        // pendingSeekFraction is applied when duration becomes available (STATE_READY).
        maybeApplyPendingSeek(controller)
    }
}
