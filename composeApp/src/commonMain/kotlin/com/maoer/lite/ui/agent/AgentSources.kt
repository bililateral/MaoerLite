package com.maoer.lite.ui.agent

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.maoer.lite.data.agent.*
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.*

internal fun JsonObject.text(key: String) = this[key]?.jsonPrimitive?.content.orEmpty()

internal fun visibleSources(run: AgentRun): List<JsonObject> = run.sources.ifEmpty {
    // Legacy conversations remain readable without fabricating a server citation binding.
    run.results.flatMap { receipt -> (receipt.result["items"] as? JsonArray).orEmpty().map { it.jsonObject } }
        .filter { it.text("evidence").isNotBlank() }
        .distinctBy { it.text("id") + it.text("source_url") + it.text("evidence") }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun AgentSources(run: AgentRun, session: AgentSession, selected: Int?, onSelected: (Int?) -> Unit,
                          openPodcast: (String) -> Unit, selectEpisode: (JsonObject) -> Unit, enabled: Boolean) {
    val sources = remember(run.sources, run.results) { visibleSources(run) }
    val candidates = remember(run.text, run.results) {
        run.results.filterNot { it.call_id.startsWith("refresh_") }.flatMap {
            (it.result["items"] as? JsonArray).orEmpty().map { item -> item.jsonObject }
        }.filter { it.text("kind") in listOf("podcast", "episode") && it.text("title").isNotBlank() &&
            run.text.contains(it.text("title")) && it.text("evidence").isBlank() }.distinctBy { it.text("id") }
    }
    if (sources.isEmpty() && candidates.isEmpty()) return
    var expanded by remember(run.id) { mutableStateOf(false) }
    var detail by remember(run.id) { mutableStateOf<JsonObject?>(null) }
    var linkError by remember { mutableStateOf("") }
    val uri = LocalUriHandler.current
    LaunchedEffect(selected) { if (selected != null) expanded = true }
    Surface(color = Color.White, shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) {
        Column(Modifier.padding(12.dp)) {
            TextButton(onClick = { expanded = !expanded; if (!expanded) onSelected(null) }) {
                Text("${if (expanded) "▾" else "▸"} 资料来源 · ${sources.size} 条" + if (candidates.isNotEmpty()) " · 节目候选 ${candidates.size}" else "")
            }
            if (expanded) {
                sources.forEach { source ->
                    val number = source["number"]?.jsonPrimitive?.intOrNull
                    val bring = remember { BringIntoViewRequester() }
                    LaunchedEffect(expanded, selected) {
                        if (number != null && selected == number) { withFrameNanos { }; bring.bringIntoView() }
                    }
                    Column(Modifier.fillMaxWidth().bringIntoViewRequester(bring)
                        .background(if (number != null && selected == number) Color(0xFFFFF1DB) else Color.Transparent)
                        .padding(vertical = 10.dp, horizontal = 4.dp)) {
                        Text((number?.let { "[$it] " } ?: "") + source.text("title"), style = MaterialTheme.typography.titleSmall)
                        val used = number != null && Regex("\\[$number\\](?!\\()") .containsMatchIn(run.text)
                        Text(if (used) "回答引用" else "其他检索资料", fontSize = 11.sp, color = Color.Gray)
                        if (source.text("podcast_title").isNotBlank()) Text(source.text("podcast_title"), fontSize = 12.sp)
                        Text(source.text("evidence_field"), fontSize = 12.sp, color = Color.Gray)
                        if (source.text("published").isNotBlank()) Text("发布时间：${source.text("published")}", fontSize = 11.sp, color = Color.Gray)
                        if (source.text("fetched_display").isNotBlank()) Text("读取时间：${source.text("fetched_display")}", fontSize = 11.sp, color = Color.Gray)
                        Text(when (source.text("freshness")) {
                            "cached" -> "缓存资料，可能不是最新内容"
                            "recent_cache" -> "近期缓存资料"
                            else -> "检索时取得的资料"
                        }, fontSize = 11.sp, color = Color.Gray)
                        SelectionContainer { Text(source.text("evidence"), fontSize = 14.sp, lineHeight = 21.sp) }
                        if (source["evidence_truncated"]?.jsonPrimitive?.booleanOrNull == true) Text("以上为原文节选", fontSize = 11.sp, color = Color.Gray)
                        Row {
                            if (source.text("document_id").isNotBlank() && number != null)
                                TextButton(onClick = { detail = source }) { Text("查看原文") }
                            val url = source.text("original_url").ifBlank { source.text("source_url") }
                            if (url.startsWith("https://") || url.startsWith("http://"))
                                TextButton(onClick = { runCatching { uri.openUri(url) }.onFailure { linkError = "无法打开来源链接，请检查浏览器" } }) {
                                    Text(if (source.text("kind") == "episode" && source.text("original_url").isBlank()) "打开 RSS 来源" else "打开原网页")
                                }
                        }
                        if (source.text("kind") == "episode") TextButton(onClick = { selectEpisode(source) }, enabled = enabled) { Text("选择这一集") }
                    }
                    HorizontalDivider()
                }
                candidates.forEach { card ->
                    Text(card.text("title"), Modifier.padding(top = 10.dp))
                    Text("目录候选，不作为内容事实依据", fontSize = 11.sp, color = Color.Gray)
                    if (card.text("kind") == "podcast") TextButton(onClick = { openPodcast(card.text("id")) }) { Text("查看节目") }
                    else TextButton(onClick = { selectEpisode(card) }, enabled = enabled) { Text("选择这一集") }
                }
                if (linkError.isNotBlank()) Text(linkError, color = MaterialTheme.colorScheme.error)
            }
        }
    }
    detail?.let { source ->
        var result by remember(source) { mutableStateOf<AgentSourceText?>(null) }
        var error by remember(source) { mutableStateOf("") }
        LaunchedEffect(source) {
            try { result = session.sourceText(run.id, source.getValue("number").jsonPrimitive.int) }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { error = "原文暂时无法读取；仍可查看引用节选或打开原网页。" }
        }
        AlertDialog(onDismissRequest = { detail = null }, title = { Text(source.text("title"), maxLines = 3) },
            text = { Column {
                Text(if (result?.complete == true) "已保存的来源正文（非音频全文）" else "来源节选或正文提取结果", fontSize = 12.sp)
                LazyColumn(Modifier.heightIn(max = 400.dp)) {
                    if (result == null) item { Text(error.ifBlank { "正在读取原文…" }) }
                    else items(result!!.text.chunked(1800)) { piece -> SelectionContainer { Text(piece) } }
                }
            } }, confirmButton = { TextButton(onClick = { detail = null }) { Text("关闭") } })
    }
}
