package com.maoer.lite.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp

internal fun descriptionLinks(text: String): List<IntRange> =
    Regex("https?://[^\\s<>\\\"，。；、！？【】（）()]+", RegexOption.IGNORE_CASE).findAll(text).mapNotNull { match ->
        val url = match.value.trimEnd('.', ',', ';', ':', '!', '?', ']', '}')
        if (url.length > 8) match.range.first until match.range.first + url.length else null
    }.toList()

/** RSS stays plain text. Each actual http(s) URL has an accessible link action. */
@Composable
fun PodcastDescription(description: String) {
    val uri = LocalUriHandler.current
    val paragraphs = remember(description) { description.split(Regex("\n{2,}")) }
    var error by remember(description) { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        paragraphs.forEach { paragraph ->
            Column {
                var start = 0
                descriptionLinks(paragraph).forEach { range ->
                    paragraph.substring(start, range.first).trim().takeIf { it.isNotEmpty() }?.let {
                        Text(it, style = MaterialTheme.typography.bodyMedium, color = Color.Gray)
                    }
                    val url = paragraph.substring(range)
                    TextButton(onClick = { error = runCatching { uri.openUri(url) }.isFailure },
                        shape = RectangleShape,
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp)) {
                        Text(url, style = MaterialTheme.typography.bodyMedium,
                            textDecoration = TextDecoration.Underline)
                    }
                    start = range.last + 1
                }
                paragraph.substring(start).trim().takeIf { it.isNotEmpty() }?.let {
                    Text(it, style = MaterialTheme.typography.bodyMedium, color = Color.Gray)
                }
            }
        }
        if (error) Text("暂时无法打开链接", color = MaterialTheme.colorScheme.error)
    }
}
