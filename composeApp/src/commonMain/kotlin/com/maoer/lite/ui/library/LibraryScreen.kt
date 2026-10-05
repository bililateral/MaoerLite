package com.maoer.lite.ui.library

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.graphics.Color
import cafe.adriel.voyager.core.screen.Screen
import cafe.adriel.voyager.navigator.LocalNavigator
import com.maoer.lite.data.download.*
import com.maoer.lite.data.library.ListeningLibrary
import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.data.model.Audio
import com.maoer.lite.data.podcast.*
import com.maoer.lite.ui.detail.DetailScreen
import com.maoer.lite.ui.components.NowPlayingBar
import com.maoer.lite.ui.components.PodcastCover
import com.maoer.lite.ui.podcast.PodcastScreen
import org.koin.compose.koinInject

object LibraryScreen : Screen {
    @OptIn(ExperimentalMaterial3Api::class)
    @Composable
    override fun Content() {
        val palette = MaterialTheme.colorScheme.copy(primary = Color(0xFFB83D36), onPrimary = Color.White,
            secondaryContainer = Color(0xFFF3EAE1), surface = Color(0xFFFAF7F2), surfaceVariant = Color(0xFFF1EBE4),
            onSurface = Color(0xFF292623), onSurfaceVariant = Color(0xFF716A63))
        MaterialTheme(colorScheme = palette) { LibraryContent() }
    }

    @OptIn(ExperimentalMaterial3Api::class)
    @Composable
    private fun LibraryContent() {
        val navigator = LocalNavigator.current
        val library = koinInject<ListeningLibrary>()
        val downloads = koinInject<Downloads>()
        val manager = koinInject<PlayerManager>()
        val entries by library.entries.collectAsState()
        val bookmarks by library.bookmarks.collectAsState()
        val ready by library.ready.collectAsState()
        val libraryError by library.error.collectAsState()
        val downloadError by downloads.error.collectAsState()
        val downloaded by downloads.items.collectAsState()
        val current by manager.currentAudio.collectAsState()
        var tab by rememberSaveable { mutableStateOf(0) }
        var query by rememberSaveable { mutableStateOf("") }
        var removeDownload by remember { mutableStateOf<DownloadItem?>(null) }
        var notice by remember { mutableStateOf<String?>(null) }
        val index = remember { PodcastSearchIndex(BuiltInPodcasts.sources) }
        val matchingPodcasts = remember(query) { index.search(query).map { it.id }.toSet() }
        fun matches(audio: Audio) = query.isBlank() || audio.title.contains(query.trim(), true) ||
            audio.author.contains(query.trim(), true) || audio.podcastId in matchingPodcasts
        val selected = entries.filter { entry ->
            matches(entry.audio) && when (tab) {
                0 -> entry.lastPlayedAt > 0 && !entry.completed
                1 -> entry.favorite
                else -> entry.lastPlayedAt > 0 || entry.completed
            }
        }
        val sourceBookmarks = BuiltInPodcasts.sources.filter { it.id in bookmarks && it.id in matchingPodcasts }
        val selectedDownloads = downloaded.filter { matches(it.audio) }
        val listState = remember(tab, query) { LazyListState() }
        fun play(audio: Audio, queue: List<Audio>) {
            manager.setPlaylist(queue)
            manager.play(audio)
            navigator?.push(DetailScreen)
        }
        Scaffold(topBar = { TopAppBar(title = { Text("我的收听") }, navigationIcon = {
            IconButton(onClick = { navigator?.pop() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "返回") }
        }) }, bottomBar = { NowPlayingBar() }) { padding ->
            Column(Modifier.fillMaxSize().padding(padding)) {
                OutlinedTextField(query, { query = it }, singleLine = true,
                    placeholder = { Text("搜索节目或分集") }, modifier = Modifier.fillMaxWidth().padding(16.dp))
                ScrollableTabRow(selectedTabIndex = tab, edgePadding = 8.dp) {
                    listOf("继续收听", "收藏", "历史", "下载").forEachIndexed { i, title ->
                        Tab(selected = tab == i, onClick = { tab = i }, text = { Text(title) })
                    }
                }
                (notice ?: libraryError ?: downloadError)?.let { message ->
                    Text(message, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(16.dp))
                }
                if (!ready) LinearProgressIndicator(Modifier.fillMaxWidth())
                LazyColumn(Modifier.weight(1f), state = listState, contentPadding = PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (tab == 1 && sourceBookmarks.isNotEmpty()) {
                        item { Text("收藏的节目", style = MaterialTheme.typography.titleMedium) }
                        items(sourceBookmarks, key = { "podcast-${it.id}" }) { source ->
                            Card(onClick = { navigator?.push(PodcastScreen(source.id)) }, modifier = Modifier.fillMaxWidth()) {
                                Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                                    PodcastCover(source.coverUrl, Modifier.size(56.dp))
                                    Text(source.title, Modifier.weight(1f).padding(12.dp))
                                    TextButton(onClick = { library.toggleBookmark(source.id) }) { Text("取消收藏") }
                                }
                            }
                        }
                    }
                    if (tab == 3) {
                        item { Text("已下载 ${downloaded.count { it.status == DownloadStatus.COMPLETE }} 集 · ${downloaded.sumOf { it.bytes } / 1024 / 1024} MB",
                            style = MaterialTheme.typography.titleMedium) }
                        items(selectedDownloads, key = { it.audio.id }) { item ->
                            Card(Modifier.fillMaxWidth()) {
                                Column(Modifier.padding(16.dp)) {
                                    Text(item.audio.title, style = MaterialTheme.typography.titleSmall)
                                    Text(downloadLabel(item), style = MaterialTheme.typography.bodySmall)
                                    Row {
                                        if (item.status == DownloadStatus.COMPLETE) TextButton(onClick = {
                                            play(item.audio, selectedDownloads.filter { it.status == DownloadStatus.COMPLETE }.map { it.audio })
                                        }) { Text("离线播放") }
                                        if (item.status == DownloadStatus.FAILED) TextButton(onClick = { downloads.enqueue(item.audio) }) { Text("重试下载") }
                                        TextButton(onClick = {
                                            if (current?.id == item.audio.id) notice = "请先切换到其他分集，再删除此文件"
                                            else removeDownload = item
                                        }) { Text(if (item.status == DownloadStatus.COMPLETE) "删除文件" else "取消并移除") }
                                    }
                                }
                            }
                        }
                    } else {
                        if (tab == 1 && selected.isNotEmpty()) item { Text("收藏的分集", style = MaterialTheme.typography.titleMedium) }
                        items(selected, key = { it.audio.id }) { entry ->
                            Card(onClick = { play(entry.audio, selected.map { it.audio }) }, modifier = Modifier.fillMaxWidth()) {
                                Column(Modifier.padding(16.dp)) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        PodcastCover(entry.audio.coverUrl, Modifier.size(56.dp))
                                        Column(Modifier.weight(1f).padding(start = 12.dp)) {
                                            Text(entry.audio.title, maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall)
                                            Text(entry.audio.author, style = MaterialTheme.typography.bodySmall)
                                            Text(if (entry.completed) "已听完 · 点击重听" else "听至 ${formatDuration(entry.positionMs / 1000)}",
                                                style = MaterialTheme.typography.labelMedium)
                                        }
                                    }
                                    ListeningActions(entry.audio)
                                    if (tab == 2) TextButton(onClick = {
                                        if (current?.id == entry.audio.id) notice = "请先切换到其他分集，再移除此记录"
                                        else library.removeHistory(entry.audio)
                                    }) { Text("移除历史") }
                                }
                            }
                        }
                    }
                    if ((tab == 3 && selectedDownloads.isEmpty()) ||
                        (tab != 3 && selected.isEmpty() && (tab != 1 || sourceBookmarks.isEmpty()))) item {
                        Column(Modifier.fillMaxWidth().padding(vertical = 40.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                            Text(if (query.isNotBlank()) "没有匹配的记录" else when (tab) {
                                0 -> "还没有继续收听的分集"
                                1 -> "收藏喜欢的节目和分集，留着慢慢听"
                                2 -> "播放后会在这里留下足迹"
                                else -> "下载的分集会保存在这里"
                            })
                            if (tab == 3) Text("在支持下载的分集页面添加", style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
        removeDownload?.let { item -> AlertDialog(onDismissRequest = { removeDownload = null },
            title = { Text("移除下载？") }, text = { Text("将移除下载任务和本地音频，收藏与收听进度会保留。") },
            confirmButton = { TextButton(onClick = { downloads.remove(item.audio.id); removeDownload = null }) { Text("移除") } },
            dismissButton = { TextButton(onClick = { removeDownload = null }) { Text("保留") } }) }
    }
}
