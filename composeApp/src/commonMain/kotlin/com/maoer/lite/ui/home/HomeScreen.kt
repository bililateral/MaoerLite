package com.maoer.lite.ui.home

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.staggeredgrid.LazyVerticalStaggeredGrid
import androidx.compose.foundation.lazy.staggeredgrid.StaggeredGridCells
import androidx.compose.foundation.lazy.staggeredgrid.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.SkipPrevious
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import cafe.adriel.voyager.core.model.ScreenModel
import cafe.adriel.voyager.core.model.screenModelScope
import cafe.adriel.voyager.core.screen.Screen
import cafe.adriel.voyager.koin.getScreenModel
import cafe.adriel.voyager.navigator.LocalNavigator
import coil3.compose.AsyncImage
import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.data.model.Audio
import com.maoer.lite.data.repository.MaoerRepository
import com.maoer.lite.ui.detail.DetailScreen
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.koin.compose.koinInject

/**
 * 首页的 ViewModel。
 * 负责从仓库获取推荐音频列表。
 */
class HomeViewModel(private val repository: MaoerRepository) : ScreenModel {
    private val _state = MutableStateFlow<List<Audio>>(emptyList())
    val state = _state.asStateFlow()
    
    private val _loading = MutableStateFlow(true)
    val loading = _loading.asStateFlow()

    init {
        // 初始化时加载数据
        screenModelScope.launch {
            _loading.value = true
            _state.value = repository.getRecommendAudios()
            _loading.value = false
        }
    }
}

/**
 * 应用程序的主屏幕。
 * 显示音频项目的交错网格和持久的底部播放器栏。
 */
object HomeScreen : Screen {
    @OptIn(ExperimentalMaterial3Api::class)
    @Composable
    override fun Content() {
        val viewModel = getScreenModel<HomeViewModel>()
        val playerManager = koinInject<PlayerManager>()
        val list by viewModel.state.collectAsState()
        val loading by viewModel.loading.collectAsState()
        val navigator = LocalNavigator.current
        
        // 观察全局播放器状态
        val currentAudio by playerManager.currentAudio.collectAsState()
        val isPlaying by playerManager.isPlaying.collectAsState()
        val progress by playerManager.progress.collectAsState()
        
        val MaoerPink = Color(0xFFD32F2F)

        Scaffold(
            topBar = {
                CenterAlignedTopAppBar(
                    title = { Text("猫耳Lite", color = Color.White, fontWeight = FontWeight.Bold) },
                    colors = TopAppBarDefaults.centerAlignedTopAppBarColors(containerColor = MaoerPink)
                )
            },
            bottomBar = {
                // 持久底部播放器栏
                // 仅当有活动音频轨道（启动时加载）时可见
                if (currentAudio != null) {
                    BottomPlayerBar(
                        audio = currentAudio!!,
                        isPlaying = isPlaying,
                        progress = progress,
                        onPlayPause = { if (isPlaying) playerManager.pause() else playerManager.resume() },
                        onNext = { playerManager.next() },
                        onPrev = { playerManager.previous() },
                        onClick = { navigator?.push(DetailScreen(currentAudio!!)) } // 点击导航到详情页
                    )
                }
            }
        ) { padding ->
            Box(modifier = Modifier.fillMaxSize().padding(padding)) {
                if (loading) {
                    CircularProgressIndicator(
                        modifier = Modifier.align(Alignment.Center),
                        color = MaoerPink
                    )
                } else {
                    // 音频项目的瀑布流/交错网格布局
                    LazyVerticalStaggeredGrid(
                        columns = StaggeredGridCells.Fixed(2),
                        contentPadding = PaddingValues(16.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalItemSpacing = 12.dp,
                        modifier = Modifier.fillMaxSize()
                    ) {
                        items(list) { audio ->
                            AudioItem(audio) {
                                // 点击项目时：
                                // 1. 更新播放列表上下文，以便“下一首/上一首”正常工作。
                                // 2. 开始播放选定的音频。
                                // 3. 不要导航；让用户使用底部栏进入详情页。
                                playerManager.setPlaylist(list)
                                playerManager.play(audio)
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun BottomPlayerBar(
    audio: Audio,
    isPlaying: Boolean,
    progress: Float,
    onPlayPause: () -> Unit,
    onNext: () -> Unit,
    onPrev: () -> Unit,
    onClick: () -> Unit
) {
    Surface(
        onClick = onClick,
        shadowElevation = 8.dp,
        tonalElevation = 8.dp,
        color = MaterialTheme.colorScheme.surfaceContainer,
        shape = RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp)
    ) {
        Column {
            // 进度条 (细条)
            LinearProgressIndicator(
                progress = { progress },
                modifier = Modifier.fillMaxWidth().height(2.dp),
                color = Color(0xFFD32F2F),
                trackColor = Color.Transparent
            )
            
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(8.dp)
                    .height(64.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                // 封面
                AsyncImage(
                    model = audio.coverUrl,
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier
                        .size(48.dp)
                        .clip(RoundedCornerShape(8.dp))
                )
                
                Spacer(Modifier.width(12.dp))
                
                // 标题
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = audio.title,
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.Bold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                    Text(
                        text = audio.author,
                        style = MaterialTheme.typography.labelSmall,
                        color = Color.Gray,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }
                
                // 控制按钮
                IconButton(onClick = onPrev) {
                    Icon(Icons.Default.SkipPrevious, "Previous")
                }
                
                IconButton(
                    onClick = onPlayPause,
                    modifier = Modifier
                        .background(Color(0xFFD32F2F).copy(alpha = 0.1f), CircleShape)
                ) {
                    Icon(
                        if (isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow,
                        "Play/Pause",
                        tint = Color(0xFFD32F2F)
                    )
                }
                
                IconButton(onClick = onNext) {
                    Icon(Icons.Default.SkipNext, "Next")
                }
            }
        }
    }
}

@Composable
fun AudioItem(audio: Audio, onClick: () -> Unit) {
    Card(
        onClick = onClick,
        elevation = CardDefaults.cardElevation(4.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White),
        shape = RoundedCornerShape(12.dp)
    ) {
        Column {
            Box {
                AsyncImage(
                    model = audio.coverUrl,
                    contentDescription = null,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier
                        .fillMaxWidth()
                        .aspectRatio(1f) // 保持正方形封面
                )
                Icon(
                    imageVector = Icons.Default.PlayArrow,
                    contentDescription = null,
                    tint = Color.White,
                    modifier = Modifier
                        .align(Alignment.BottomEnd)
                        .padding(8.dp)
                        .size(24.dp)
                        .background(Color.Black.copy(alpha = 0.5f), RoundedCornerShape(50))
                        .padding(4.dp)
                )
            }
            
            Column(modifier = Modifier.padding(12.dp)) {
                Text(
                    text = audio.title,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    maxLines = 2
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    text = audio.author,
                    style = MaterialTheme.typography.bodySmall,
                    color = Color.Gray
                )
            }
        }
    }
}