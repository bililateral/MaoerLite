package com.maoer.lite.service

import android.app.PendingIntent
import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
import androidx.annotation.OptIn
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.DefaultMediaNotificationProvider
import androidx.media3.session.LibraryResult
import androidx.media3.session.MediaLibraryService
import androidx.media3.session.MediaSession
import androidx.media3.session.SessionError
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionResult
import com.google.common.collect.ImmutableList
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import com.maoer.lite.MainActivity
import com.maoer.lite.R
import com.maoer.lite.data.local.KeyValueStorage
import com.maoer.lite.data.local.getAppDataStore
import com.maoer.lite.data.manager.PlaybackSnapshot
import com.maoer.lite.data.manager.asAudio
import com.maoer.lite.data.manager.PlaybackSnapshotStore
import com.maoer.lite.data.manager.SleepTimerPolicy
import com.maoer.lite.data.manager.SleepTimerState
import com.maoer.lite.data.model.Audio
import com.maoer.lite.data.library.ListeningLibrary
import com.maoer.lite.data.library.ListeningPolicy
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.Dispatchers

/**
 * Android 后台播放 Service（Media3）。
 *
 * 关键点：
 * - Service 内部持有真正的 ExoPlayer 实例（音频焦点、解码、缓冲都在这里）。
 * - 对外通过 [MediaLibrarySession] 暴露播放控制能力；UI 侧通过 [androidx.media3.session.MediaController]
 *   连接到此 Service 来控制播放，并接收通知栏/锁屏/蓝牙耳机等系统媒体控件的指令。
 * - 这里配置了 [DefaultMediaNotificationProvider]，用于系统通知与锁屏控制展示。
 *
 * 生命周期说明：
 * - 本项目选择“从 Recents 划掉任务也继续播放”（符合多数音频类 App 的预期），因此不主动在 onTaskRemoved 停止播放。
 * - 播放中每 5 秒及暂停/切歌/定位时异步保存快照，供下次启动恢复。
 */
class MaoerPlaybackService : MediaLibraryService() {

    private lateinit var mediaLibrarySession: MediaLibrarySession
    private lateinit var player: ExoPlayer
    private val sleepTimer = SleepTimerPolicy()
    private var publishedTimer: SleepTimerState? = null
    private var timerJob: Job? = null
    private val persistenceScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var progressPersistence: Job? = null
    private val snapshots = Channel<PlaybackSnapshot>(Channel.CONFLATED)
    private val snapshotStore by lazy { PlaybackSnapshotStore(KeyValueStorage(getAppDataStore())) }
    private val library: ListeningLibrary by lazy { org.koin.core.context.GlobalContext.get().get() }
    private data class ProgressWrite(val audio: Audio, val position: Long, val duration: Long, val at: Long, val ended: Boolean, val restarted: Boolean = false)
    private val progressWrites = Channel<ProgressWrite>(Channel.UNLIMITED)
    private val heardIds = mutableSetOf<String>()
    private var lastDuration = 0L

    @OptIn(UnstableApi::class)
    override fun onCreate() {
        super.onCreate()

        // 1. 初始化 ExoPlayer
        // AudioAttributes 确保我们获得正确的音频焦点 (USAGE_MEDIA)
        val audioAttributes = AudioAttributes.Builder()
            .setContentType(C.AUDIO_CONTENT_TYPE_MUSIC)
            .setUsage(C.USAGE_MEDIA)
            .build()

        player = ExoPlayer.Builder(this)
            .setAudioAttributes(audioAttributes, true) // true = handleAudioFocus
            .setHandleAudioBecomingNoisy(true) // 耳机拔出时暂停
            .setWakeMode(C.WAKE_MODE_LOCAL)
            .build()
        // 保留产品既有规则：列表循环，最后一集结束自动回到第一集。
        player.repeatMode = Player.REPEAT_MODE_ALL
        persistenceScope.launch {
            for (snapshot in snapshots) {
                try { snapshotStore.save(snapshot) }
                catch (cancelled: CancellationException) { throw cancelled }
                catch (error: Exception) { android.util.Log.w("MaoerPlayback", "Snapshot persistence failed", error) }
            }
        }
        persistenceScope.launch {
            for (write in progressWrites) {
                try { library.record(write.audio, write.position, write.duration, write.at, write.ended, write.restarted) }
                catch (e: CancellationException) { throw e }
                catch (e: Exception) { library.reportSaveFailure(); android.util.Log.w("MaoerPlayback", "Episode progress persistence failed", e) }
            }
        }
        player.addListener(object : Player.Listener {
            override fun onPositionDiscontinuity(oldPosition: Player.PositionInfo, newPosition: Player.PositionInfo, reason: Int) {
                val old = oldPosition.mediaItem ?: return
                if (old.mediaId != newPosition.mediaItem?.mediaId || reason == Player.DISCONTINUITY_REASON_AUTO_TRANSITION) {
                    if (old.mediaId in heardIds) progressWrites.trySend(ProgressWrite(old.asAudio(), oldPosition.positionMs,
                        lastDuration, System.currentTimeMillis(), reason == Player.DISCONTINUITY_REASON_AUTO_TRANSITION))
                    lastDuration = 0
                }
            }
            override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
                // Read the destination before queuing any restarted/zero-position write.
                // A single-item automatic repeat has just ended that same episode: restart it.
                val resumesDestination = reason == Player.MEDIA_ITEM_TRANSITION_REASON_SEEK ||
                    reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO
                val resumePosition = if (resumesDestination &&
                    (reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO || player.currentPosition == 0L))
                    ListeningPolicy.resumePosition(library.entries.value.find { it.audio.id == mediaItem?.mediaId }) else 0L
                if (resumePosition > 0) player.seekTo(resumePosition)
                if (mediaItem != null && reason != Player.MEDIA_ITEM_TRANSITION_REASON_PLAYLIST_CHANGED) {
                    progressWrites.trySend(ProgressWrite(mediaItem.asAudio(), player.currentPosition.coerceAtLeast(0),
                        0, System.currentTimeMillis(), false, restarted = true))
                }
            }
            override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
                if (!playWhenReady && reason == Player.PLAY_WHEN_READY_CHANGE_REASON_END_OF_MEDIA_ITEM) {
                    cancelSleepTimer()
                }
            }
            override fun onEvents(player: Player, events: Player.Events) {
                if (player.isPlaying) player.currentMediaItem?.mediaId?.let(heardIds::add)
                if (player.duration > 0) lastDuration = player.duration
                if (events.contains(Player.EVENT_MEDIA_ITEM_TRANSITION) ||
                    events.contains(Player.EVENT_IS_PLAYING_CHANGED) ||
                    events.contains(Player.EVENT_POSITION_DISCONTINUITY) ||
                    events.contains(Player.EVENT_PLAYBACK_PARAMETERS_CHANGED)) persistPlaybackSnapshot()
            }
        })
        progressPersistence = persistenceScope.launch {
            while (isActive) { delay(5000); if (player.isPlaying) persistPlaybackSnapshot() }
        }

        // 2. 创建点击通知时的 Intent (跳转到首页)
        val sessionActivityPendingIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP
            },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        // 3. 构建 MediaSession
        mediaLibrarySession = MediaLibrarySession.Builder(this, player, LibrarySessionCallback())
            .setSessionActivity(sessionActivityPendingIntent)
            .build()
        publishSleepTimer()
        timerJob = persistenceScope.launch {
            while (isActive) {
                delay(500)
                if (sleepTimer.consumeExpiry(SystemClock.elapsedRealtime())) {
                    player.pause()
                    player.pauseAtEndOfMediaItems = false
                }
                if (::mediaLibrarySession.isInitialized) publishSleepTimer()
            }
        }

        // 4. Media notification + lockscreen controls (Media3).
        // This also promotes the service to foreground when required during playback.
        // 说明：Android 13+ 需要 POST_NOTIFICATIONS 运行时权限，否则用户可能看不到媒体通知。
        setMediaNotificationProvider(DefaultMediaNotificationProvider(this).apply {
            setSmallIcon(R.mipmap.ic_launcher)
        })
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaLibrarySession? {
        return mediaLibrarySession
    }

    override fun onDestroy() {
        // Service 被系统销毁时尽量保存一次播放快照。注意：该回调不保证一定发生（进程被直接杀死时可能不会）。
        persistPlaybackSnapshot()
        progressPersistence?.cancel()
        timerJob?.cancel()
        snapshots.close()
        progressWrites.close()
        mediaLibrarySession.release()
        player.release()
        super.onDestroy()
    }

    private fun persistPlaybackSnapshot() {
        // Do not overwrite the saved position with a temporary zero during offline restoration.
        if (player.playbackState == Player.STATE_IDLE || player.playbackState == Player.STATE_BUFFERING) return
        val currentId = player.currentMediaItem?.mediaId ?: return
        // Capture all ExoPlayer values on its application (main) thread before async disk IO.
        val queue = (0 until player.mediaItemCount).map { index ->
            val item = player.getMediaItemAt(index)
            item.asAudio()
        }
        snapshots.trySend(PlaybackSnapshot(queue, currentId, player.currentPosition.coerceAtLeast(0), player.playbackParameters.speed))
        if (currentId in heardIds) queue.find { it.id == currentId }?.let {
            progressWrites.trySend(ProgressWrite(it, player.currentPosition, player.duration.coerceAtLeast(0),
                System.currentTimeMillis(), player.playbackState == Player.STATE_ENDED ||
                    (player.pauseAtEndOfMediaItems && player.duration > 0 && player.currentPosition >= player.duration)))
        }
    }

    private fun cancelSleepTimer() {
        sleepTimer.cancel()
        player.pauseAtEndOfMediaItems = false
        publishSleepTimer()
    }

    private fun publishSleepTimer() {
        val state = sleepTimer.state(SystemClock.elapsedRealtime())
        if (state == publishedTimer) return
        publishedTimer = state
        mediaLibrarySession.setSessionExtras(Bundle().apply {
            putLong(PlaybackCommands.REMAINING_MS, state.remainingMs)
            putBoolean(PlaybackCommands.END_OF_EPISODE, state.endOfEpisode)
        })
    }

    // 回调处理，用于处理来自 Controller 的自定义命令或浏览请求
    private inner class LibrarySessionCallback : MediaLibrarySession.Callback {
        override fun onConnect(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
        ): MediaSession.ConnectionResult {
            val connectionResult = super.onConnect(session, controller)
            val sessionCommands = connectionResult.availableSessionCommands
                .buildUpon()
                .add(SessionCommand(PlaybackCommands.SLEEP_TIMER, Bundle.EMPTY))
                .build()
            return MediaSession.ConnectionResult.accept(sessionCommands, connectionResult.availablePlayerCommands)
        }

        override fun onCustomCommand(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
            customCommand: SessionCommand,
            args: Bundle,
        ): ListenableFuture<SessionResult> {
            if (customCommand.customAction != PlaybackCommands.SLEEP_TIMER) {
                return Futures.immediateFuture(SessionResult(SessionError.ERROR_NOT_SUPPORTED))
            }
            val minutes = args.getInt(PlaybackCommands.MINUTES, 0)
            when {
                minutes == 0 -> cancelSleepTimer()
                minutes == -1 -> {
                    sleepTimer.atEpisodeEnd()
                    player.pauseAtEndOfMediaItems = true
                }
                minutes in 1..180 -> {
                    sleepTimer.afterMinutes(minutes, SystemClock.elapsedRealtime())
                    player.pauseAtEndOfMediaItems = false
                }
                else -> return Futures.immediateFuture(SessionResult(SessionError.ERROR_BAD_VALUE))
            }
            publishSleepTimer()
            return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
        }

        // 这是一个 MediaLibraryService，通常需要实现 onGetLibraryRoot 等方法供 MediaBrowser 浏览。
        // 但对于我们的用例（只是为了后台播放和通知），我们可以返回空结果。
        @OptIn(UnstableApi::class)
        override fun onGetLibraryRoot(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            params: LibraryParams?,
        ): ListenableFuture<LibraryResult<MediaItem>> {
            return Futures.immediateFuture(LibraryResult.ofError(SessionError.ERROR_NOT_SUPPORTED))
        }

        @OptIn(UnstableApi::class)
        override fun onGetItem(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            mediaId: String,
        ): ListenableFuture<LibraryResult<MediaItem>> {
             return Futures.immediateFuture(LibraryResult.ofError(SessionError.ERROR_NOT_SUPPORTED))
        }

        override fun onSubscribe(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            parentId: String,
            params: LibraryParams?,
        ): ListenableFuture<LibraryResult<Void>> {
             return Futures.immediateFuture(LibraryResult.ofVoid())
        }

        @OptIn(UnstableApi::class)
        override fun onGetChildren(
            session: MediaLibrarySession,
            browser: MediaSession.ControllerInfo,
            parentId: String,
            page: Int,
            pageSize: Int,
            params: LibraryParams?,
        ): ListenableFuture<LibraryResult<ImmutableList<MediaItem>>> {
             return Futures.immediateFuture(LibraryResult.ofError(SessionError.ERROR_NOT_SUPPORTED))
        }
    }
}
