package com.maoer.lite.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
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
import cafe.adriel.voyager.navigator.LocalNavigator
import coil3.compose.AsyncImage
import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.data.model.Audio
import com.maoer.lite.ui.detail.DetailScreen
import org.koin.compose.koinInject

@Composable
fun PodcastCover(url: String, modifier: Modifier = Modifier) {
    Box(modifier.clip(RoundedCornerShape(12.dp)).background(MaterialTheme.colorScheme.surfaceVariant), contentAlignment = Alignment.Center) {
        Icon(Icons.Default.Headphones, contentDescription = null, modifier = Modifier.size(32.dp))
        AsyncImage(model = url, contentDescription = null, contentScale = ContentScale.Crop, modifier = Modifier.matchParentSize())
    }
}

@Composable
fun NowPlayingBar() {
    val manager = koinInject<PlayerManager>()
    val audio by manager.currentAudio.collectAsState()
    val playing by manager.isPlaying.collectAsState()
    val progress by manager.progress.collectAsState()
    val navigator = LocalNavigator.current
    audio?.let {
        BottomPlayerBar(it, playing, progress,
            { if (playing) manager.pause() else manager.resume() },
            { manager.next() }, { manager.previous() }, { navigator?.push(DetailScreen) })
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
                modifier = Modifier.fillMaxWidth().height(4.dp),
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
                    Icon(Icons.Default.SkipPrevious, "上一集")
                }

                IconButton(
                    onClick = onPlayPause,
                    modifier = Modifier
                        .background(Color(0xFFD32F2F).copy(alpha = 0.1f), CircleShape)
                ) {
                    Icon(
                        if (isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow,
                        if (isPlaying) "暂停" else "播放",
                        tint = Color(0xFFD32F2F)
                    )
                }

                IconButton(onClick = onNext) {
                    Icon(Icons.Default.SkipNext, "下一集")
                }
            }
        }
    }
}
