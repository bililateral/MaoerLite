package com.maoer.lite.ui.agent

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import cafe.adriel.voyager.core.screen.Screen
import cafe.adriel.voyager.navigator.LocalNavigator
import com.maoer.lite.data.agent.*
import com.maoer.lite.ui.home.NowPlayingBar
import com.maoer.lite.ui.podcast.PodcastScreen
import kotlinx.serialization.json.*
import org.koin.compose.koinInject

object AgentScreen : Screen {
    @OptIn(ExperimentalMaterial3Api::class)
    @Composable override fun Content() {
        val session = koinInject<AgentSession>()
        val state by session.state.collectAsState()
        val navigator = LocalNavigator.current
        var input by rememberSaveable { mutableStateOf("") }
        var settings by remember { mutableStateOf(false) }
        val list = rememberLazyListState()
        val accent = Color(0xFFB83D36)
        val last = state.history.turns.lastOrNull()?.run
        val pending = state.history.deletePending || state.history.turns.lastOrNull()?.let { it.run?.terminal != true } == true || state.history.cancelPending != null
        LaunchedEffect(state.ready) {
            if (state.ready && state.connected && pending && !state.working) session.reconnect()
        }
        LaunchedEffect(state.history.turns.size) {
            if (state.history.turns.isNotEmpty()) list.animateScrollToItem(state.history.turns.lastIndex)
        }
        Scaffold(containerColor = Color(0xFFFAF7F2), topBar = {
            TopAppBar(title = { Column { Text("语音助手", fontWeight = FontWeight.Bold); Text("找节目 · 控制播放", fontSize = 11.sp) } },
                navigationIcon = { IconButton(onClick = { navigator?.pop() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "返回") } },
                actions = {
                    IconButton(onClick = { session.newConversation() }, enabled = !state.working && !pending) { Icon(Icons.Default.AddComment, "新对话") }
                    IconButton(onClick = { settings = true }, enabled = !state.working) { Icon(Icons.Default.Settings, "助手连接设置") }
                }, colors = TopAppBarDefaults.topAppBarColors(containerColor = Color(0xFFFAF7F2)))
        }, bottomBar = {
            Column(Modifier.fillMaxWidth().navigationBarsPadding().imePadding()) {
                if (state.notice.isNotBlank()) Text(state.notice, Modifier.padding(horizontal = 16.dp, vertical = 6.dp), color = accent, fontSize = 12.sp)
                Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(when {
                        state.history.deletePending -> if (state.working) "正在删除旧对话…" else "旧对话删除待确认"
                        state.history.cancelPending != null -> "停止请求待同步"
                        last?.status == "retry_wait" -> "${agentError(last.error)}，等待 ${last.retry_after} 秒后重试"
                        last?.status == "awaiting_tools" -> "正在执行手机操作"
                        state.working -> "正在回复…"
                        last?.status == "failed" -> agentError(last.error)
                        last?.status == "cancelled" -> "已停止；已执行的操作保持实际状态"
                        !state.connected -> "请通过右上角设置连接服务"
                        else -> ""
                    }, Modifier.weight(1f), fontSize = 12.sp, color = Color(0xFF756D65))
                    if (state.history.deletePending) {
                        if (!state.working) TextButton(onClick = { session.reconnect() }) { Text("重试删除", color = accent) }
                    }
                    else if (state.working) TextButton(onClick = session::stop) { Text("停止", color = accent) }
                    else if (pending || last?.status == "failed") TextButton(onClick = { session.reconnect(retry = last?.status == "failed") }) {
                        Text(if (last?.status == "failed") "重试当前步骤" else "恢复连接", color = accent)
                    }
                }
                NowPlayingBar()
                Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.Bottom) {
                    OutlinedTextField(value = input, onValueChange = { if (it.length <= 2000) input = it },
                        placeholder = { Text("想听什么，告诉我") }, maxLines = 4, modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(22.dp))
                    IconButton(onClick = { session.send(input); input = "" }, enabled = state.ready && state.connected && !state.working && !pending && input.isNotBlank()) {
                        Icon(Icons.AutoMirrored.Filled.Send, "发送消息", tint = if (state.connected && !pending && !state.working) accent else Color.Gray)
                    }
                }
            }
        }) { padding ->
            if (state.history.turns.isEmpty()) {
                Column(Modifier.fillMaxSize().padding(padding).verticalScroll(rememberScrollState()).padding(24.dp), verticalArrangement = Arrangement.Center) {
                    Icon(Icons.Default.AutoAwesome, null, tint = accent, modifier = Modifier.size(36.dp))
                    Text("让好声音，\n更懂你的心意。", fontSize = 28.sp, lineHeight = 38.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(vertical = 20.dp))
                    Text("用文字找节目、控制播放，或聊聊你想听的内容。", color = Color(0xFF756D65))
                    listOf("帮我找岩中花述", "推荐几个科技节目", "现在播放的是什么？").forEach { question ->
                        OutlinedButton(onClick = { input = question }, modifier = Modifier.padding(top = 12.dp)) { Text(question, color = accent) }
                    }
                }
            } else LazyColumn(state = list, modifier = Modifier.fillMaxSize().padding(padding),
                contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
                items(state.history.turns, key = { it.id }) { turn ->
                    Column(Modifier.fillMaxWidth()) {
                        Surface(color = Color(0xFFF1E3DC), shape = RoundedCornerShape(18.dp), modifier = Modifier.align(Alignment.End)) {
                            Text(turn.question, Modifier.padding(14.dp))
                        }
                        val run = turn.run
                        if (!run?.text.isNullOrBlank()) Surface(color = Color.White, shape = RoundedCornerShape(18.dp), modifier = Modifier.padding(top = 10.dp)) {
                            Text(run!!.text, Modifier.padding(14.dp), lineHeight = 24.sp)
                        }
                        run?.results?.forEach { receipt ->
                            val cards = receipt.result["items"] as? JsonArray
                            cards?.forEach { item ->
                                val card = item.jsonObject
                                val title = card["title"]?.jsonPrimitive?.content.orEmpty()
                                val kind = card["kind"]?.jsonPrimitive?.content.orEmpty()
                                Surface(color = Color.White, shape = RoundedCornerShape(14.dp), modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) {
                                    Column(Modifier.padding(14.dp)) {
                                        Text(title, fontWeight = FontWeight.SemiBold)
                                        Text(card[if (kind == "podcast") "category" else "published"]?.jsonPrimitive?.content.orEmpty(), fontSize = 12.sp, color = Color.Gray)
                                        if (kind == "podcast") TextButton(onClick = { navigator?.push(PodcastScreen(card.getValue("id").jsonPrimitive.content)) }) { Text("查看节目", color = accent) }
                                        else TextButton(onClick = { input = "请播放《$title》这一集" }, enabled = !state.working) { Text("选择这一集", color = accent) }
                                    }
                                }
                            }
                            if (cards == null) Text(receipt.result["note"]?.jsonPrimitive?.content.orEmpty(), color = Color(0xFF756D65), fontSize = 12.sp, modifier = Modifier.padding(top = 8.dp))
                        }
                    }
                }
            }
        }
        if (settings) AgentConnectionDialog(session) { settings = false }
    }
}
