package com.maoer.lite.ui.detail

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.SkipPrevious
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
import cafe.adriel.voyager.core.screen.Screen
import cafe.adriel.voyager.navigator.LocalNavigator
import coil3.compose.AsyncImage
import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.ui.library.ListeningActions
import org.koin.compose.koinInject

// Voyager saves screens in Android instance state; keep live audio objects in PlayerManager.
object DetailScreen : Screen {
    @OptIn(ExperimentalMaterial3Api::class)
    @Composable
    override fun Content() {
        val navigator = LocalNavigator.current
        val playerManager = koinInject<PlayerManager>()
        
        // Observe Player State
        // 这里直接观察全局 PlayerManager 的 StateFlow：
        // - 详情页与首页底栏会天然保持同步；
        // - 进度 progress 是 0..1 的比例，与 Slider 的 value 一致。
        val currentAudio by playerManager.currentAudio.collectAsState()
        val isPlaying by playerManager.isPlaying.collectAsState()
        val progress by playerManager.progress.collectAsState()
        
        val audio = currentAudio
        if (audio == null) {
            Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center) {
                Text("还没有正在收听的节目")
                TextButton(onClick = { navigator?.pop() }) { Text("返回") }
            }
            return
        }
        
        val MaoerPink = Color(0xFFD32F2F)
        val scrollState = rememberScrollState()
        
        val durationMs by playerManager.durationMs.collectAsState()
        val positionMs by playerManager.positionMs.collectAsState()
        val playbackError by playerManager.playbackError.collectAsState()
        val buffering by playerManager.buffering.collectAsState()
        var dragPosition by remember(audio.id) { mutableStateOf<Float?>(null) }

        Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
            // 1. 视差背景图 (Parallax Background)
            AsyncImage(
                model = audio.coverUrl,
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(400.dp)
                    .graphicsLayer {
                        translationY = -scrollState.value * 0.5f
                        alpha = 1f - (scrollState.value / 600f).coerceAtMost(0.6f)
                    }
            )
            
            // 2. 氛围遮罩 (Atmosphere Overlay)
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(400.dp)
                    .background(
                        Brush.verticalGradient(
                            colors = listOf(Color.Transparent, Color.Black.copy(alpha = 0.8f))
                        )
                    )
            )

            // 4. 可滚动内容 (Scrollable Content)
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(scrollState)
            ) {
                // 留白，让背景露出来
                Spacer(Modifier.height(320.dp))
                
                // 内容卡片
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp),
                    color = MaterialTheme.colorScheme.surface
                ) {
                    Column(
                        modifier = Modifier.padding(24.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        // 标题区
                        Text(audio.title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                        Spacer(Modifier.height(8.dp))
                        Text(audio.author, style = MaterialTheme.typography.bodyLarge, color = Color.Gray)
                        ListeningActions(audio)
                        val libraryError by playerManager.libraryError.collectAsState()
                        libraryError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                        
                        Spacer(Modifier.height(32.dp))
                        
                        // 封面图 (小图)
                        Card(
                            elevation = CardDefaults.cardElevation(8.dp),
                            shape = RoundedCornerShape(16.dp),
                            modifier = Modifier.size(200.dp)
                        ) {
                            AsyncImage(
                                model = audio.coverUrl,
                                contentDescription = null,
                                contentScale = ContentScale.Crop,
                                modifier = Modifier.fillMaxSize()
                            )
                        }

                        Spacer(Modifier.height(48.dp))
                        
                        // 进度条
                        Slider(
                            modifier = Modifier.semantics { contentDescription = "播放进度" },
                            value = dragPosition ?: progress,
                            enabled = durationMs > 0,
                            onValueChange = { dragPosition = it },
                            onValueChangeFinished = { dragPosition?.let(playerManager::seekTo); dragPosition = null },
                            colors = SliderDefaults.colors(thumbColor = MaoerPink, activeTrackColor = MaoerPink)
                        )
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text(com.maoer.lite.data.podcast.formatDuration((dragPosition?.let { (it * durationMs).toLong() } ?: positionMs) / 1000), style = MaterialTheme.typography.bodySmall)
                            Text(if (durationMs > 0) com.maoer.lite.data.podcast.formatDuration(durationMs / 1000) else "--:--", style = MaterialTheme.typography.bodySmall)
                        }
                        if (buffering) {
                            LinearProgressIndicator(Modifier.fillMaxWidth())
                            Text("正在缓冲…", style = MaterialTheme.typography.labelMedium)
                        }
                        playbackError?.let { message ->
                            Text(message, color = MaterialTheme.colorScheme.error)
                            TextButton(onClick = playerManager::resume) { Text("重试播放") }
                        }

                        Spacer(Modifier.height(24.dp))
                        
                        // 播放控制区
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(32.dp)
                        ) {
                            // 上一个
                            IconButton(
                                onClick = { playerManager.previous() },
                                modifier = Modifier.size(48.dp)
                            ) {
                                Icon(Icons.Default.SkipPrevious, "上一集", modifier = Modifier.size(32.dp))
                            }

                            // 播放/暂停
                            IconButton(
                                onClick = { if (isPlaying) playerManager.pause() else playerManager.resume() },
                                modifier = Modifier.size(72.dp).clip(CircleShape).background(MaoerPink)
                            ) {
                                Icon(
                                    imageVector = if (isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow,
                                    contentDescription = if (isPlaying) "暂停" else "播放",
                                    tint = Color.White,
                                    modifier = Modifier.size(40.dp)
                                )
                            }
                            
                            // 下一个
                            IconButton(
                                onClick = { playerManager.next() },
                                modifier = Modifier.size(48.dp)
                            ) {
                                Icon(Icons.Default.SkipNext, "下一集", modifier = Modifier.size(32.dp))
                            }
                        }
                        
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
                            TextButton(onClick = { playerManager.seekBy(-15_000) }, enabled = durationMs > 0) { Text("后退 15 秒") }
                            TextButton(onClick = { playerManager.seekBy(30_000) }, enabled = durationMs > 0) { Text("前进 30 秒") }
                        }
                        Spacer(Modifier.height(16.dp))
                        PlaybackTools(playerManager)
                        Spacer(Modifier.height(32.dp))
                        Text(
                            audio.description.ifBlank { "本集暂无简介" },
                            style = MaterialTheme.typography.bodyMedium,
                            color = Color.Gray
                        )
                        Spacer(Modifier.height(200.dp))
                    }
                }
            }

            // 3. 顶部导航栏
            TopAppBar(
                title = {},
                navigationIcon = {
                    IconButton(
                        onClick = { navigator?.pop() },
                        colors = IconButtonDefaults.iconButtonColors(containerColor = Color.Black.copy(alpha = 0.3f))
                    ) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "返回", tint = Color.White)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Color.Transparent)
            )
        }
    }
}
