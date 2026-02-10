package com.maoer.lite.service

import android.app.PendingIntent
import android.content.Intent
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
import com.google.common.collect.ImmutableList
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
import com.maoer.lite.MainActivity
import com.maoer.lite.R
import com.maoer.lite.data.local.KeyValueStorage
import com.maoer.lite.data.local.getAppDataStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking

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
 * - 仅在 Service 真正销毁时（onDestroy）保存一次播放快照，供下次启动恢复。
 */
class MaoerPlaybackService : MediaLibraryService() {

    private lateinit var mediaLibrarySession: MediaLibrarySession
    private lateinit var player: Player
    private val storage by lazy { KeyValueStorage(getAppDataStore()) }

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
            .build()
        // 让“上一首/下一首”在队列两端也可用（循环播放列表），并避免播到最后一首直接结束停住。
        player.repeatMode = Player.REPEAT_MODE_ALL

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
        mediaLibrarySession.release()
        player.release()
        super.onDestroy()
    }

    private fun persistPlaybackSnapshot() {
        val mediaId = player.currentMediaItem?.mediaId ?: return
        val durationMs = player.duration
        val positionMs = player.currentPosition
        if (durationMs == C.TIME_UNSET || durationMs <= 0L) return

        val progress = (positionMs.toDouble() / durationMs.toDouble()).toFloat().coerceIn(0f, 1f)

        // 从生命周期回调触发时用同步写入：提高“被回收前落盘成功”的概率。
        // 这里写入的是 0..1 的进度比例，方便跨平台与 UI 统一（不依赖毫秒）。
        runBlocking(Dispatchers.IO) {
            storage.saveString("last_audio_id", mediaId)
            storage.saveString("last_audio_progress", progress.toString())
        }
    }

    // 回调处理，用于处理来自 Controller 的自定义命令或浏览请求
    private class LibrarySessionCallback : MediaLibrarySession.Callback {
        override fun onConnect(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
        ): MediaSession.ConnectionResult {
            val connectionResult = super.onConnect(session, controller)
            val sessionCommands = connectionResult.availableSessionCommands
                .buildUpon()
                // 如果需要自定义命令，可以在这里添加
                .build()
            return MediaSession.ConnectionResult.accept(sessionCommands, connectionResult.availablePlayerCommands)
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
