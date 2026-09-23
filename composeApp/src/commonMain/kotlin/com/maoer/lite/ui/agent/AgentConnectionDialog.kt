package com.maoer.lite.ui.agent

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.maoer.lite.data.agent.*
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.launch

@Composable
fun AgentConnectionDialog(session: AgentSession, dismiss: () -> Unit) {
    var url by remember { mutableStateOf("http://127.0.0.1:8787") }
    var token by remember { mutableStateOf("") }
    var useSsh by remember { mutableStateOf(true) }
    var host by remember { mutableStateOf("") }
    var port by remember { mutableStateOf("22") }
    var user by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var pin by remember { mutableStateOf("") }
    var candidate by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var ready by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    var progress by remember { mutableStateOf("") }
    var advanced by remember { mutableStateOf(false) }
    val keyboard = LocalSoftwareKeyboardController.current
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) {
        try {
            val config = session.connection()
            url = config.url; token = config.token
            if (config.token.isNotEmpty()) useSsh = config.ssh != null
            config.ssh?.let { useSsh = true; host = it.host; port = it.port.toString(); user = it.username; password = it.password; pin = it.fingerprint }
            ready = true
        } catch (_: Exception) { error = "连接配置无法读取，请检查设备安全存储" }
    }
    fun clearPin() { pin = ""; candidate = ""; error = "" }
    fun ssh() = AgentSshConfig(host.trim(), port.toIntOrNull() ?: 0, user.trim(), password, pin)
    AlertDialog(onDismissRequest = { if (!busy) dismiss() }, title = { Text("助手服务连接") }, text = {
        Column(Modifier.heightIn(max = 480.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row {
                FilterChip(selected = useSsh, onClick = { if (!busy) useSsh = true }, label = { Text("SSH 隧道") })
                Spacer(Modifier.width(8.dp))
                FilterChip(selected = !useSsh, onClick = { if (!busy) useSsh = false }, label = { Text("直接连接") })
            }
            if (useSsh) {
                Text("手机通过 SSH 连接服务器，无需电脑转发。任务结束后自动关闭隧道。", style = MaterialTheme.typography.bodySmall)
                OutlinedTextField(host, { host = it; clearPin() }, enabled = !busy, label = { Text("SSH 服务器") }, singleLine = true)
                OutlinedTextField(port, { port = it; clearPin() }, enabled = !busy, label = { Text("SSH 端口") }, singleLine = true)
                OutlinedTextField(user, { user = it }, enabled = !busy, label = { Text("SSH 用户名") }, singleLine = true)
                OutlinedTextField(password, { password = it }, enabled = !busy, label = { Text("SSH 密码") }, singleLine = true, visualTransformation = PasswordVisualTransformation())
                TextButton(enabled = !busy && ready, onClick = {
                    keyboard?.hide()
                    busy = true; progress = "正在读取服务器指纹…"; error = ""; candidate = ""
                    scope.launch {
                        try { candidate = session.probeSsh(ssh()) }
                        catch (e: CancellationException) { throw e }
                        catch (e: AgentTransportException) { error = agentError(e.code) }
                        catch (_: Exception) { error = "请检查 SSH 地址、端口和用户名" }
                        finally { busy = false; progress = "" }
                    }
                }) { Text(if (busy) "正在读取…" else "读取服务器指纹") }
                if (pin.isNotEmpty()) Text("已确认指纹：\n$pin", style = MaterialTheme.typography.bodySmall)
                if (candidate.isNotEmpty()) {
                    Text("请与服务器提供的 SHA256 指纹核对：\n$candidate", style = MaterialTheme.typography.bodySmall)
                    TextButton(enabled = !busy, onClick = { pin = candidate; candidate = ""; error = "" }) { Text("已核对，信任此指纹") }
                }
            }
            if (useSsh) TextButton(enabled = !busy, onClick = { advanced = !advanced }) { Text(if (advanced) "收起高级设置" else "高级设置 · 服务地址") }
            if (!useSsh || advanced) OutlinedTextField(url, { url = it; error = "" }, enabled = !busy, label = { Text(if (useSsh) "服务器内服务地址" else "服务地址") }, singleLine = true)
            OutlinedTextField(token, { token = it.trim() }, enabled = !busy, label = { Text("服务访问令牌") }, singleLine = true, visualTransformation = PasswordVisualTransformation())
            Text("访问令牌不是模型 API Key。密码和令牌由 Android 安全存储加密保存。", style = MaterialTheme.typography.bodySmall)
        }
    }, confirmButton = {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (error.isNotEmpty()) Text(error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
            else if (progress.isNotEmpty()) Text(progress, style = MaterialTheme.typography.bodySmall)
            else if (useSsh && pin.isEmpty()) Text("先读取并确认服务器指纹，再保存连接。", style = MaterialTheme.typography.bodySmall)
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                TextButton(enabled = !busy, onClick = dismiss) { Text("取消") }
                TextButton(enabled = ready && !busy, onClick = {
                    keyboard?.hide()
                    scope.launch {
                        busy = true; error = ""; progress = "正在验证连接和访问令牌…"
                        try {
                            val config = AgentConnection(url, token, if (useSsh) ssh() else null)
                            config.validationError()?.let { error = it; return@launch }
                            session.configure(config)
                            dismiss()
                        } catch (_: TimeoutCancellationException) { error = "连接验证超时，请确认手机已连接内网或 VPN 后重试。" }
                        catch (e: CancellationException) { throw e }
                        catch (e: AgentTransportException) { error = agentError(e.code) }
                        catch (e: AgentApiException) { error = if (e.code == "unauthorized") "服务访问令牌不正确，请复制服务器对应的 agent-server.token 内容。" else "服务验证失败，请检查服务地址后重试。" }
                        catch (e: IllegalStateException) { error = e.message ?: "连接未保存，请重试。" }
                        catch (_: Exception) { error = "无法验证服务连接，请检查内网或 VPN、服务地址及服务是否运行；原配置未更改。" }
                        finally { busy = false; progress = "" }
                    }
                }) { Text(if (busy) "请稍候…" else "验证并保存") }
            }
        }
    })
}
