package com.maoer.lite.ui.podcast

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.FavoriteBorder
import androidx.compose.foundation.lazy.LazyListState
import com.maoer.lite.data.library.ListeningLibrary
import com.maoer.lite.ui.library.ListeningActions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import cafe.adriel.voyager.core.model.ScreenModel
import cafe.adriel.voyager.core.model.rememberScreenModel
import cafe.adriel.voyager.core.model.screenModelScope
import cafe.adriel.voyager.core.screen.Screen
import cafe.adriel.voyager.navigator.LocalNavigator
import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.data.podcast.*
import com.maoer.lite.ui.detail.DetailScreen
import com.maoer.lite.ui.home.NowPlayingBar
import com.maoer.lite.ui.home.PodcastCover
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import org.koin.compose.koinInject

class PodcastViewModel(private val id: String, private val repository: PodcastRepository) : ScreenModel {
    val source = repository.source(id)
    private val _feed = MutableStateFlow<PodcastFeed?>(null)
    val feed = _feed.asStateFlow()
    private val _loading = MutableStateFlow(false)
    val loading = _loading.asStateFlow()
    private val _error = MutableStateFlow<String?>(null)
    val error = _error.asStateFlow()
    init { refresh() }
    fun refresh() {
        if (_loading.value) return
        _loading.value = true
        screenModelScope.launch {
            try {
                if (_feed.value == null) _feed.value = repository.cached(id)
                _error.value = null
                _feed.value = repository.refresh(id)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) {
                _error.value = if (_feed.value == null) "节目暂时加载失败，请检查网络后重试。"
                    else "更新失败，正在显示上次缓存的分集。"
            } finally { _loading.value = false }
        }
    }
}

data class PodcastScreen(val sourceId: String) : Screen {
    @OptIn(ExperimentalMaterial3Api::class)
    @Composable
    override fun Content() {
        val repository = koinInject<PodcastRepository>()
        val model = rememberScreenModel { PodcastViewModel(sourceId, repository) }
        val manager = koinInject<PlayerManager>()
        val library = koinInject<ListeningLibrary>()
        val bookmarks by library.bookmarks.collectAsState()
        val entries by library.entries.collectAsState()
        val libraryReady by library.ready.collectAsState()
        val navigator = LocalNavigator.current
        val feed by model.feed.collectAsState()
        val loading by model.loading.collectAsState()
        val error by model.error.collectAsState()
        var query by remember { mutableStateOf("") }
        var reverse by remember { mutableStateOf(false) }
        var count by remember { mutableStateOf(50) }
        val episodes = remember(feed, query, reverse) {
            val all = feed?.episodes.orEmpty().filter { it.title.contains(query.trim(), true) }
            if (reverse) all.reversed() else all
        }
        LaunchedEffect(query, reverse) { count = 50 }
        val listState = remember(query, reverse) { LazyListState() }
        Scaffold(
            topBar = { TopAppBar(title = { Text(model.source.title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                navigationIcon = { IconButton(onClick = { navigator?.pop() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "返回") } },
                actions = {
                    IconButton(onClick = { library.toggleBookmark(sourceId) }, enabled = libraryReady) {
                        Icon(if (sourceId in bookmarks) Icons.Default.Favorite else Icons.Default.FavoriteBorder,
                            if (sourceId in bookmarks) "取消收藏节目" else "收藏节目")
                    }
                    IconButton(onClick = model::refresh, enabled = !loading) { Icon(Icons.Default.Refresh, "刷新节目") }
                }) },
            bottomBar = { NowPlayingBar() },
        ) { padding ->
            LazyColumn(Modifier.fillMaxSize().padding(padding), state = listState, contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                item {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        PodcastCover(feed?.coverUrl ?: model.source.coverUrl, Modifier.size(96.dp))
                        Column(Modifier.padding(start = 16.dp).weight(1f)) {
                            Text(feed?.title ?: model.source.title, style = MaterialTheme.typography.titleLarge)
                            Text(feed?.author.orEmpty().ifBlank { model.source.category }, style = MaterialTheme.typography.bodyMedium)
                            Text("${feed?.episodes?.size ?: 0} 集", style = MaterialTheme.typography.labelLarge)
                        }
                    }
                }
                feed?.description?.takeIf { it.isNotBlank() }?.let { description ->
                    item { Text(description, maxLines = 5, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium) }
                }
                if (loading) item { LinearProgressIndicator(Modifier.fillMaxWidth()) }
                error?.let { message -> item {
                    Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp)) {
                        Text(message)
                        TextButton(onClick = model::refresh, enabled = !loading) { Text("重试") }
                    } }
                } }
                if (feed != null) item {
                    Text("按当前分集顺序列表循环播放", style = MaterialTheme.typography.labelSmall)
                    if (!model.source.downloadAllowed) Text("此节目暂不提供离线下载", style = MaterialTheme.typography.labelSmall)
                    OutlinedTextField(query, { query = it }, label = { Text("搜索本节目分集") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text("分集 · ${episodes.size}")
                        TextButton(onClick = { reverse = !reverse }) { Text(if (reverse) "倒序 · 切换" else "原始顺序 · 切换") }
                    }
                }
                items(episodes.take(count), key = { it.id }) { episode ->
                    Card(onClick = {
                        val current = feed ?: return@Card
                        val queue = episodes.map { it.asAudio(current) }
                        manager.setPlaylist(queue)
                        val audio = episode.asAudio(current)
                        manager.play(audio)
                        navigator?.push(DetailScreen)
                    }, modifier = Modifier.fillMaxWidth()) {
                        Column {
                        Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(episode.title, style = MaterialTheme.typography.titleSmall, maxLines = 3, overflow = TextOverflow.Ellipsis)
                                if (episode.published.isNotBlank()) Text(episode.published, style = MaterialTheme.typography.labelSmall)
                                Text(if (episode.durationSeconds > 0) formatDuration(episode.durationSeconds) else "时长待加载", style = MaterialTheme.typography.labelMedium)
                                entries.find { it.audio.id == episode.id }?.let { entry ->
                                    if (entry.completed) Text("已听完", style = MaterialTheme.typography.labelSmall)
                                    else if (entry.positionMs > 0) Text("听至 ${formatDuration(entry.positionMs / 1000)}", style = MaterialTheme.typography.labelSmall)
                                }
                            }
                            Icon(Icons.Default.PlayArrow, "播放${episode.title}", Modifier.padding(start = 12.dp))
                        }
                        feed?.let { current -> Box(Modifier.padding(horizontal = 12.dp, vertical = 4.dp)) { ListeningActions(episode.asAudio(current), showAvailability = false) } }
                        }
                    }
                }
                if (count < episodes.size) item { TextButton(onClick = { count += 50 }, modifier = Modifier.fillMaxWidth()) { Text("加载更多分集") } }
                if (feed != null && episodes.isEmpty()) item { Text("没有匹配的分集") }
            }
        }
    }
}
