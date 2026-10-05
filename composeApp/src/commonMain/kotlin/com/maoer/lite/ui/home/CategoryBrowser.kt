package com.maoer.lite.ui.home

import com.maoer.lite.ui.components.PodcastCover

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Headphones
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.maoer.lite.data.podcast.PodcastSource
import com.maoer.lite.data.podcast.PodcastSearchIndex
import kotlinx.coroutines.launch

/** The category rail and the grouped catalog share the same ordered section indexes. */
@Composable
fun CategoryBrowser(
    searchIndex: PodcastSearchIndex,
    query: String,
    onQueryChange: (String) -> Unit,
    listState: LazyListState,
    railState: LazyListState,
    onOpenPodcast: (PodcastSource) -> Unit,
    modifier: Modifier = Modifier,
) {
    val ink = Color(0xFF292623)
    val accent = Color(0xFFB83D36)
    val background = Color(0xFFF6F5F3)
    val groups = remember(searchIndex, query) {
        searchIndex.search(query).groupBy { it.category }.entries.toList()
    }
    val selectedIndex by remember(listState, groups.size) {
        derivedStateOf { listState.firstVisibleItemIndex.coerceAtMost((groups.size - 1).coerceAtLeast(0)) }
    }
    val scope = rememberCoroutineScope()
    LaunchedEffect(selectedIndex, groups.size) {
        if (groups.isNotEmpty() && railState.layoutInfo.visibleItemsInfo.none { it.index == selectedIndex }) {
            railState.scrollToItem(selectedIndex)
        }
    }
    Column(modifier.background(background)) {
        OutlinedTextField(value = query, onValueChange = onQueryChange, singleLine = true,
            placeholder = { Text("节目、分类、拼音或首字母", fontSize = 14.sp) },
            leadingIcon = { Icon(Icons.Default.Search, null) },
            trailingIcon = {
                if (query.isNotEmpty()) IconButton(onClick = { onQueryChange("") }) {
                    Icon(Icons.Default.Close, "清空分类搜索")
                }
            },
            shape = RoundedCornerShape(28.dp),
            colors = OutlinedTextFieldDefaults.colors(
                focusedContainerColor = Color.White, unfocusedContainerColor = Color.White,
                focusedBorderColor = accent, unfocusedBorderColor = Color(0xFFDDD9D4), cursorColor = accent,
                focusedTextColor = ink, unfocusedTextColor = ink,
            ),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 16.dp))
        if (groups.isEmpty()) {
            Column(Modifier.fillMaxSize().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center) {
                Icon(Icons.Default.Search, null, tint = Color(0xFF918A83), modifier = Modifier.size(36.dp))
                Text("没有找到匹配的节目", color = ink, modifier = Modifier.padding(top = 16.dp))
                TextButton(onClick = { onQueryChange("") }) { Text("查看全部分类", color = accent) }
            }
        } else {
            BoxWithConstraints(Modifier.fillMaxWidth().weight(1f)) {
                val viewportHeight = maxHeight
                Row(Modifier.fillMaxSize()) {
                    LazyColumn(state = railState, modifier = Modifier.width(92.dp).fillMaxHeight(),
                        contentPadding = PaddingValues(bottom = 16.dp)) {
                        itemsIndexed(groups, key = { _, group -> group.key }) { index, group ->
                            val selected = selectedIndex == index
                            Column {
                                Box(Modifier.fillMaxWidth().heightIn(min = 68.dp)
                                    .background(if (selected) Color.White else Color.Transparent)
                                    .selectable(selected = selected, role = Role.Tab,
                                        onClick = { scope.launch { listState.scrollToItem(index) } }),
                                    contentAlignment = Alignment.Center) {
                                    if (selected) Box(Modifier.align(Alignment.CenterStart).width(3.dp).height(24.dp)
                                        .background(accent, RoundedCornerShape(2.dp)))
                                    Text(group.key, modifier = Modifier.padding(horizontal = 10.dp, vertical = 18.dp),
                                        color = if (selected) accent else Color(0xFF89837D), fontSize = 14.sp,
                                        fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal)
                                }
                                HorizontalDivider(Modifier.padding(horizontal = 12.dp), color = Color(0xFFE4E1DC))
                            }
                        }
                    }
                    LazyColumn(state = listState, modifier = Modifier.weight(1f).fillMaxHeight(),
                        contentPadding = PaddingValues(start = 8.dp, end = 12.dp, bottom = 12.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        itemsIndexed(groups, key = { _, group -> group.key }) { index, group ->
                            // Last section fills the viewport so even the final category can align to the top.
                            Box(if (index == groups.lastIndex) Modifier.heightIn(min = viewportHeight - 12.dp) else Modifier) {
                                Surface(shape = RoundedCornerShape(18.dp), color = Color.White, contentColor = ink) {
                                    Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 18.dp)) {
                                        Row(verticalAlignment = Alignment.CenterVertically) {
                                            Icon(Icons.Default.Headphones, null, tint = accent, modifier = Modifier.size(18.dp))
                                            Text(group.key, fontSize = 17.sp, fontWeight = FontWeight.Bold,
                                                modifier = Modifier.weight(1f).padding(start = 6.dp))
                                            Text("${group.value.size} 个", fontSize = 11.sp, color = Color(0xFF89837D))
                                        }
                                        Spacer(Modifier.height(20.dp))
                                        group.value.chunked(3).forEach { row ->
                                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                                row.forEach { source ->
                                                    Surface(onClick = { onOpenPodcast(source) }, color = Color.Transparent,
                                                        shape = RoundedCornerShape(10.dp), modifier = Modifier.weight(1f)) {
                                                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                                            PodcastCover(source.coverUrl, Modifier.fillMaxWidth().aspectRatio(1f))
                                                            Text(source.title, fontSize = 12.sp, lineHeight = 18.sp,
                                                                maxLines = 2, minLines = 2, overflow = TextOverflow.Ellipsis,
                                                                modifier = Modifier.fillMaxWidth().padding(top = 8.dp, bottom = 16.dp))
                                                        }
                                                    }
                                                }
                                                repeat(3 - row.size) { Spacer(Modifier.weight(1f)) }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
