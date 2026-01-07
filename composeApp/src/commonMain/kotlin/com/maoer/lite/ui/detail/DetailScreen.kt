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
import cafe.adriel.voyager.core.screen.Screen
import cafe.adriel.voyager.navigator.LocalNavigator
import coil3.compose.AsyncImage
import com.maoer.lite.data.model.Audio
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive

data class DetailScreen(val audio: Audio) : Screen {
    @OptIn(ExperimentalMaterial3Api::class)
    @Composable
    override fun Content() {
        val navigator = LocalNavigator.current
        val MaoerPink = Color(0xFFD32F2F)
        val scrollState = rememberScrollState()
        
        // 播放状态逻辑
        var isPlaying by remember { mutableStateOf(false) }
        var progress by remember { mutableStateOf(0.3f) }
        val totalSeconds = remember(audio.duration) {
            val parts = audio.duration.split(":")
            if (parts.size == 2) {
                (parts[0].toIntOrNull() ?: 0) * 60 + (parts[1].toIntOrNull() ?: 0)
            } else {
                300
            }
        }

        LaunchedEffect(isPlaying) {
            if (isPlaying) {
                while (isActive && progress < 1f) {
                    delay(100)
                    val step = 0.1f / totalSeconds.coerceAtLeast(1)
                    progress = (progress + step).coerceAtMost(1f)
                    if (progress >= 1f) {
                        isPlaying = false
                    }
                }
            }
        }

        Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
            // 1. 视差背景图 (Parallax Background)
            // 随着列表向上滚动，背景图以 0.5 倍速向上移动，产生视差
            AsyncImage(
                model = audio.coverUrl,
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(400.dp)
                    .graphicsLayer {
                        translationY = -scrollState.value * 0.5f // 视差核心逻辑
                        alpha = 1f - (scrollState.value / 600f).coerceAtMost(0.6f) // 慢慢变暗
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
                            value = progress,
                            onValueChange = { progress = it },
                            colors = SliderDefaults.colors(thumbColor = MaoerPink, activeTrackColor = MaoerPink)
                        )
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            val currentSecondsVal = (progress * totalSeconds).toInt()
                            val m = currentSecondsVal / 60
                            val s = currentSecondsVal % 60
                            val timeStr = "${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}"
                            Text(timeStr, style = MaterialTheme.typography.bodySmall)
                            Text(audio.duration, style = MaterialTheme.typography.bodySmall)
                        }
                        
                        Spacer(Modifier.height(24.dp))
                        
                        // 播放按钮
                        IconButton(
                            onClick = { isPlaying = !isPlaying },
                            modifier = Modifier.size(72.dp).clip(CircleShape).background(MaoerPink)
                        ) {
                            Icon(
                                imageVector = if (isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow,
                                contentDescription = null,
                                tint = Color.White,
                                modifier = Modifier.size(40.dp)
                            )
                        }
                        
                        // 音频简介，暂时是用来测试滚动效果
                        Spacer(Modifier.height(32.dp))
                        Text(
                            "简介：\n这是一个用于测试 Compose Multiplatform 视差滚动效果的演示页面。随着向上滑动，顶部的背景图会以较慢的速度移动，产生纵深感。同时，使用了 Kotlin Flow 和 LaunchedEffect 来模拟播放器的进度控制。",
                            style = MaterialTheme.typography.bodyMedium,
                            color = Color.Gray
                        )
                        Spacer(Modifier.height(200.dp)) // 撑开高度
                    }
                }
            }

            // 3. 顶部导航栏 (透明背景) - 移到最后以确保在最上层
            TopAppBar(
                title = {},
                navigationIcon = {
                    IconButton(
                        onClick = { navigator?.pop() },
                        colors = IconButtonDefaults.iconButtonColors(containerColor = Color.Black.copy(alpha = 0.3f))
                    ) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back", tint = Color.White)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Color.Transparent)
            )
        }
    }
}