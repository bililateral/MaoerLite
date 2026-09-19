package com.maoer.lite.ui.library

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.maoer.lite.data.download.Downloads
import com.maoer.lite.data.download.DownloadPolicy
import com.maoer.lite.data.download.downloadLabel
import com.maoer.lite.data.library.ListeningLibrary
import com.maoer.lite.data.model.Audio
import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.data.podcast.BuiltInPodcasts
import org.koin.compose.koinInject

@Composable
fun ListeningActions(audio: Audio, showAvailability: Boolean = true) {
    val library = koinInject<ListeningLibrary>()
    val downloads = koinInject<Downloads>()
    val manager = koinInject<PlayerManager>()
    val entries by library.entries.collectAsState()
    val ready by library.ready.collectAsState()
    val items by downloads.items.collectAsState()
    val error by library.error.collectAsState()
    val downloadError by downloads.error.collectAsState()
    val entry = entries.find { it.audio.id == audio.id }
    val item = items.find { it.audio.id == audio.id }
    Column {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
            TextButton(onClick = { library.toggleFavorite(audio) }, enabled = ready) {
                Icon(if (entry?.favorite == true) Icons.Default.Favorite else Icons.Default.FavoriteBorder, null, Modifier.size(18.dp))
                Text(if (entry?.favorite == true) "已收藏" else "收藏")
            }
            TextButton(onClick = {
                if (manager.currentAudio.value?.id == audio.id) {
                    manager.pause()
                    if (entry?.completed == true) manager.seekTo(0f)
                }
                library.setCompleted(audio, entry?.completed != true)
            }, enabled = ready) {
                Icon(Icons.Default.CheckCircle, null, Modifier.size(18.dp))
                Text(if (entry?.completed == true) "设为未听" else "标为听完")
            }
            TextButton(onClick = { downloads.enqueue(audio) },
                enabled = DownloadPolicy.allowed(audio, BuiltInPodcasts.sources) && (item == null || item.status == com.maoer.lite.data.download.DownloadStatus.FAILED)) {
                Icon(Icons.Default.Download, null, Modifier.size(18.dp))
                Text(if (item != null) "下载记录" else "下载")
            }
        }
        if (item != null) Text(downloadLabel(item), style = MaterialTheme.typography.labelSmall)
        else if (showAvailability && !DownloadPolicy.allowed(audio, BuiltInPodcasts.sources)) Text("此节目暂不提供离线下载", style = MaterialTheme.typography.labelSmall)
        (error ?: downloadError)?.let { message -> Text(message, color = MaterialTheme.colorScheme.error) }
    }
}
