package com.maoer.lite.ui.detail

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.maoer.lite.data.manager.PlaybackOptions
import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.data.podcast.formatDuration

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PlaybackTools(manager: PlayerManager) {
    val speed by manager.playbackSpeed.collectAsState()
    val timer by manager.sleepTimer.collectAsState()
    val queue by manager.queue.collectAsState()
    val current by manager.currentAudio.collectAsState()
    var sheet by remember { mutableStateOf<String?>(null) }
    val timerLabel = when {
        timer.endOfEpisode -> "本集播完"
        timer.remainingMs > 0 -> formatDuration((timer.remainingMs + 999) / 1000)
        else -> "睡眠定时"
    }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(onClick = { sheet = "speed" }, modifier = Modifier.weight(1f), contentPadding = PaddingValues(8.dp)) {
            Text("${speed}× 倍速")
        }
        OutlinedButton(onClick = { sheet = "timer" }, modifier = Modifier.weight(1f), contentPadding = PaddingValues(8.dp)) {
            Text(timerLabel)
        }
        OutlinedButton(onClick = { sheet = "queue" }, modifier = Modifier.weight(1f), contentPadding = PaddingValues(8.dp)) {
            Text("队列 ${queue.size}")
        }
    }
    if (sheet != null) {
        ModalBottomSheet(onDismissRequest = { sheet = null }, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
            when (sheet) {
                "speed" -> Column(Modifier.fillMaxWidth().padding(horizontal = 24.dp).padding(bottom = 24.dp)) {
                    Text("播放倍速", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Text("倍速会应用到后续播放，并在下次打开时保留。",
                        style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 12.dp))
                    PlaybackOptions.speeds.forEach { option ->
                        TextButton(onClick = { manager.setPlaybackSpeed(option); sheet = null }, modifier = Modifier.fillMaxWidth()) {
                            Text("${option}×", modifier = Modifier.weight(1f))
                            if (option == speed) Icon(Icons.Default.Check, "当前倍速")
                        }
                    }
                }
                "timer" -> {
                    var minutes by remember { mutableStateOf("") }
                    val customMinutes = minutes.toIntOrNull()?.takeIf { it in 1..180 }
                    Column(Modifier.fillMaxWidth().imePadding().padding(horizontal = 24.dp).padding(bottom = 24.dp)) {
                        Text("睡眠定时", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                        Text(if (timer.active) "当前：$timerLabel" else "到时自动暂停，放心听一会儿。",
                            modifier = Modifier.padding(top = 12.dp), style = MaterialTheme.typography.bodyMedium)
                        Text("暂停时继续计时；退出页面仍生效，强制关闭应用后取消。",
                            style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            listOf(15, 30, 60).forEach { value ->
                                OutlinedButton(onClick = { manager.setSleepTimer(value); sheet = null },
                                    modifier = Modifier.weight(1f), contentPadding = PaddingValues(8.dp)) { Text("$value 分钟") }
                            }
                        }
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedTextField(value = minutes, onValueChange = { value -> minutes = value.filter(Char::isDigit).take(3) },
                                label = { Text("自定义分钟（1–180）") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                                singleLine = true, modifier = Modifier.weight(1f))
                            TextButton(enabled = customMinutes != null, onClick = { manager.setSleepTimer(customMinutes); sheet = null }) { Text("开始计时") }
                        }
                        TextButton(onClick = { manager.setSleepTimer(-1); sheet = null }, modifier = Modifier.fillMaxWidth()) { Text("本集播完后暂停") }
                        TextButton(onClick = { manager.setSleepTimer(null); sheet = null }, enabled = timer.active,
                            modifier = Modifier.fillMaxWidth()) { Text("关闭定时") }
                    }
                }
                "queue" -> {
                    Text("播放队列 · ${queue.size} 集", style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold, modifier = Modifier.padding(horizontal = 24.dp))
                    Text("列表循环 · 点选分集立即播放", style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(horizontal = 24.dp, vertical = 12.dp))
                    val listState = rememberLazyListState(initialFirstVisibleItemIndex = queue.indexOfFirst { it.id == current?.id }.coerceAtLeast(0))
                    LazyColumn(state = listState, modifier = Modifier.fillMaxWidth().heightIn(max = 440.dp),
                        contentPadding = PaddingValues(bottom = 24.dp)) {
                        itemsIndexed(queue, key = { _, audio -> audio.id }) { index, audio ->
                            val selected = audio.id == current?.id
                            Surface(onClick = { manager.play(audio); sheet = null }, modifier = Modifier.fillMaxWidth(),
                                color = if (selected) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.surface) {
                                Row(Modifier.padding(horizontal = 24.dp, vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                                    Text("${index + 1}", modifier = Modifier.width(36.dp), style = MaterialTheme.typography.labelMedium)
                                    Column(Modifier.weight(1f)) {
                                        Text(audio.title, maxLines = 2, overflow = TextOverflow.Ellipsis,
                                            fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal)
                                        Text(if (selected) "当前分集" else audio.author, style = MaterialTheme.typography.bodySmall)
                                    }
                                    if (selected) Icon(Icons.Default.Check, "当前分集")
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
