package com.maoer.lite.ui.home

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import cafe.adriel.voyager.core.model.ScreenModel
import cafe.adriel.voyager.core.screen.Screen
import cafe.adriel.voyager.koin.getScreenModel
import cafe.adriel.voyager.navigator.LocalNavigator
import coil3.compose.AsyncImage
import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.data.model.Audio
import com.maoer.lite.data.podcast.PodcastRepository
import com.maoer.lite.data.podcast.PodcastSource
import com.maoer.lite.data.podcast.PodcastSearchIndex
import com.maoer.lite.ui.detail.DetailScreen
import com.maoer.lite.ui.podcast.PodcastScreen
import com.maoer.lite.ui.library.LibraryScreen
import org.koin.compose.koinInject

class HomeViewModel(repository: PodcastRepository) : ScreenModel {
    val sources = repository.sources
    val searchIndex = PodcastSearchIndex(sources)
}

object HomeScreen : Screen {
    @OptIn(ExperimentalMaterial3Api::class)
    @Composable
    override fun Content() {
        val model = getScreenModel<HomeViewModel>()
        val navigator = LocalNavigator.current
        var selectedTab by rememberSaveable { mutableStateOf(0) }
        var query by rememberSaveable { mutableStateOf("") }
        var categoryQuery by rememberSaveable { mutableStateOf("") }
        // Keep each page's scroll state across tab switches; a new search starts at the top.
        val listState = remember(query) { LazyListState() }
        val categoryListState = remember(categoryQuery) { LazyListState() }
        val categoryRailState = remember(categoryQuery) { LazyListState() }
        val sources = remember(query, model) { model.searchIndex.search(query) }
        val paper = Color(0xFFFAF7F2)
        val ink = Color(0xFF292623)
        val accent = Color(0xFFB83D36)
        val featured = sources.firstOrNull()?.takeIf { query.isBlank() }
        Scaffold(
            containerColor = paper,
            contentColor = ink,
            bottomBar = {
                Column {
                    NowPlayingBar()
                    NavigationBar(containerColor = Color.White, tonalElevation = 0.dp) {
                        listOf("首页", "分类").forEachIndexed { index, title ->
                            NavigationBarItem(selected = selectedTab == index, onClick = { selectedTab = index },
                                icon = { Icon(if (index == 0) Icons.Default.Home else Icons.Default.GridView, null) },
                                label = { Text(title) },
                                colors = NavigationBarItemDefaults.colors(
                                    selectedIconColor = accent, selectedTextColor = accent,
                                    indicatorColor = Color(0xFFF8EAE5),
                                    unselectedIconColor = Color(0xFF827A73), unselectedTextColor = Color(0xFF827A73),
                                ))
                        }
                    }
                }
            },
        ) { padding ->
            if (selectedTab == 1) {
                CategoryBrowser(searchIndex = model.searchIndex, query = categoryQuery, onQueryChange = { categoryQuery = it },
                    listState = categoryListState, railState = categoryRailState,
                    onOpenPodcast = { navigator?.push(PodcastScreen(it.id)) },
                    modifier = Modifier.fillMaxSize().padding(padding))
            } else {
                Column(Modifier.fillMaxSize().padding(padding)) {
                    Row(Modifier.fillMaxWidth().padding(start = 24.dp, end = 24.dp, top = 18.dp, bottom = 16.dp),
                        verticalAlignment = Alignment.Bottom) {
                        Column(Modifier.weight(1f)) {
                            Text("猫耳 / 声音杂志", color = accent, fontSize = 11.sp,
                                fontWeight = FontWeight.Bold, letterSpacing = 2.sp)
                            Spacer(Modifier.height(6.dp))
                            Text("听见，另一种生活", fontSize = 27.sp, fontWeight = FontWeight.Bold,
                                letterSpacing = (-1).sp)
                        }
                        IconButton(onClick = { navigator?.push(LibraryScreen) }) {
                            Icon(Icons.Default.LibraryMusic, contentDescription = "我的收听：收藏、历史与下载", tint = accent)
                        }
                    }
                    TextField(
                        value = query, onValueChange = { query = it }, singleLine = true,
                        placeholder = { Text("搜索节目、拼音或首字母", fontSize = 14.sp) },
                        leadingIcon = { Icon(Icons.Default.Search, null, modifier = Modifier.size(21.dp)) },
                        trailingIcon = {
                            if (query.isNotEmpty()) IconButton(onClick = { query = "" }) {
                                Icon(Icons.Default.Close, contentDescription = "清空搜索")
                            }
                        },
                        shape = RoundedCornerShape(16.dp),
                        colors = TextFieldDefaults.colors(
                            focusedContainerColor = Color(0xFFF0ECE6), unfocusedContainerColor = Color(0xFFF0ECE6),
                            focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent,
                            cursorColor = accent, focusedTextColor = ink, unfocusedTextColor = ink,
                        ),
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 24.dp),
                    )
                    Spacer(Modifier.height(20.dp))
                    LazyColumn(state = listState, modifier = Modifier.weight(1f),
                        contentPadding = PaddingValues(start = 24.dp, end = 24.dp, bottom = 24.dp),
                        verticalArrangement = Arrangement.spacedBy(18.dp)) {
                        if (featured != null) item(key = "featured") {
                            FeaturedPodcast(featured, onClick = { navigator?.push(PodcastScreen(featured.id)) })
                        }
                        item(key = "heading") {
                            Row(Modifier.fillMaxWidth().padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                                Column(Modifier.weight(1f)) {
                                    Text(if (query.isNotBlank()) "搜索结果" else "发现好节目",
                                        fontSize = 21.sp, fontWeight = FontWeight.Bold)
                                    if (featured != null) Text("留一点时间，给认真说话的人", color = Color(0xFF716A63),
                                        fontSize = 12.sp, modifier = Modifier.padding(top = 4.dp))
                                }
                                Text("${sources.size} 个节目", color = Color(0xFF716A63), fontSize = 12.sp)
                            }
                        }
                        if (sources.isEmpty()) item(key = "empty") {
                            Column(Modifier.fillMaxWidth().padding(vertical = 40.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                                Icon(Icons.Default.Search, null, tint = Color(0xFF999087), modifier = Modifier.size(36.dp))
                                Text("没有找到匹配的节目", fontWeight = FontWeight.Medium, modifier = Modifier.padding(top = 14.dp))
                                Text("换个关键词，再找找看", color = Color(0xFF716A63), fontSize = 13.sp,
                                    modifier = Modifier.padding(top = 6.dp))
                            }
                        }
                        items(if (featured != null) sources.drop(1) else sources, key = { it.id }) { source ->
                            Surface(onClick = { navigator?.push(PodcastScreen(source.id)) },
                                color = Color.Transparent, contentColor = ink, shape = RoundedCornerShape(16.dp),
                                modifier = Modifier.fillMaxWidth()) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    PodcastCover(source.coverUrl, Modifier.size(100.dp))
                                    Column(Modifier.weight(1f).padding(start = 16.dp, end = 8.dp)) {
                                        Text(source.category, color = accent, fontSize = 11.sp, fontWeight = FontWeight.Medium)
                                        Spacer(Modifier.height(7.dp))
                                        Text(source.title, fontSize = 17.sp, fontWeight = FontWeight.SemiBold,
                                            maxLines = 2, overflow = TextOverflow.Ellipsis, lineHeight = 23.sp)
                                        Spacer(Modifier.height(9.dp))
                                        Text("探索分集", color = Color(0xFF716A63), fontSize = 12.sp)
                                    }
                                    Icon(Icons.Default.ChevronRight, contentDescription = "查看${source.title}",
                                        tint = Color(0xFF999087), modifier = Modifier.size(20.dp))
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun FeaturedPodcast(source: PodcastSource, onClick: () -> Unit) {
    Card(onClick = onClick, shape = RoundedCornerShape(22.dp), modifier = Modifier.fillMaxWidth()) {
        Box(Modifier.fillMaxWidth().height(270.dp).background(Color(0xFF464C43))) {
            AsyncImage(model = source.coverUrl, contentDescription = null, contentScale = ContentScale.Crop,
                modifier = Modifier.matchParentSize())
            Box(Modifier.matchParentSize().background(Brush.verticalGradient(
                listOf(Color.Black.copy(alpha = 0.08f), Color.Black.copy(alpha = 0.15f), Color.Black.copy(alpha = 0.82f)))))
            Surface(color = Color(0xFFFAF7F2), shape = RoundedCornerShape(6.dp),
                modifier = Modifier.padding(18.dp)) {
                Text("本期精选 / ${source.category}", color = Color(0xFF292623), fontSize = 11.sp,
                    fontWeight = FontWeight.Bold, modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp))
            }
            Row(Modifier.align(Alignment.BottomStart).fillMaxWidth().padding(22.dp),
                verticalAlignment = Alignment.Bottom) {
                Column(Modifier.weight(1f)) {
                    Text("把世界的声音，听进生活", color = Color.White.copy(alpha = 0.85f), fontSize = 12.sp)
                    Text(source.title, color = Color.White, fontSize = 30.sp, fontWeight = FontWeight.Bold,
                        maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 6.dp))
                }
                Surface(shape = CircleShape, color = Color.White.copy(alpha = 0.2f), modifier = Modifier.size(44.dp)) {
                    Box(contentAlignment = Alignment.Center) {
                        Icon(Icons.AutoMirrored.Filled.ArrowForward, contentDescription = "查看${source.title}", tint = Color.White,
                            modifier = Modifier.size(23.dp))
                    }
                }
            }
        }
    }
}

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
