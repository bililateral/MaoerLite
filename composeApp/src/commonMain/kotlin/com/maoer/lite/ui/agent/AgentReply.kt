package com.maoer.lite.ui.agent

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.runtime.remember
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import com.mikepenz.markdown.compose.components.MarkdownComponentModel
import com.mikepenz.markdown.compose.components.markdownComponents
import com.mikepenz.markdown.m3.Markdown
import com.mikepenz.markdown.m3.markdownTypography
import com.mikepenz.markdown.model.markdownPadding
import org.intellij.markdown.ast.getTextInNode
import org.intellij.markdown.flavours.gfm.GFMElementTypes
import org.intellij.markdown.flavours.gfm.GFMTokenTypes

/** Render model text only: no remote image loader, HTML execution or tool actions. */
@Composable
internal fun AgentReply(content: String, modifier: Modifier = Modifier, citations: Set<Int> = emptySet(), onCitation: (Int) -> Unit = {}) {
    val original = LocalUriHandler.current
    val rendered = remember(content, citations) {
        Regex("(?<!!)\\[(\\d{1,3})\\](?!\\()") .replace(content) { match ->
            val number = match.groupValues[1].toInt()
            if (number in citations) "[${match.value}](maoer-source://$number)" else match.value
        }
    }
    val handler = object : UriHandler {
        override fun openUri(uri: String) {
            if (uri.startsWith("maoer-source://")) {
                uri.removePrefix("maoer-source://").toIntOrNull()?.takeIf { it in citations }?.let(onCitation)
            } else if (uri.startsWith("https://") || uri.startsWith("http://")) runCatching { original.openUri(uri) }
        }
    }
    CompositionLocalProvider(LocalUriHandler provides handler) {
    SelectionContainer {
        Markdown(content = rendered, modifier = modifier,
            typography = replyTypography(),
            padding = markdownPadding(block = 4.dp, list = 2.dp, listItemBottom = 4.dp, indentList = 12.dp),
            components = markdownComponents(
            custom = { type, model ->
                if (type == GFMElementTypes.TABLE) ReplyTable(model)
                else Text(model.node.getTextInNode(model.content).toString())
            }
        ))
    }
    }
}

/** Chat headings use emphasis, not display-sized type; all Markdown forms share this scale. */
@Composable
private fun replyTypography(tableHeader: Boolean = false) = MaterialTheme.typography.bodyLarge.copy(
    fontSize = 16.sp, lineHeight = 26.sp, letterSpacing = 0.sp,
    fontWeight = if (tableHeader) FontWeight.Medium else FontWeight.Normal,
).let { body ->
    val heading = body.copy(fontWeight = FontWeight.Medium)
    markdownTypography(
        h1 = heading, h2 = heading, h3 = heading, h4 = heading, h5 = heading, h6 = heading,
        text = body, paragraph = body, ordered = body, bullet = body, list = body, quote = body,
        code = body.copy(fontFamily = FontFamily.Monospace, fontSize = 14.sp, lineHeight = 22.sp),
    )
}

/** This Compose-compatible library version parses GFM tables but needs a renderer. */
@Composable
private fun ReplyTable(model: MarkdownComponentModel) {
    val rows = model.node.children.filter {
        it.type == GFMElementTypes.HEADER || it.type == GFMElementTypes.ROW
    }.map { row -> row.children.filter { it.type == GFMTokenTypes.CELL } }
    val columns = rows.maxOfOrNull { it.size } ?: return
    if (columns == 0) return
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        val cellWidth = maxOf(120.dp, maxWidth / columns)
        Column(Modifier.horizontalScroll(rememberScrollState())) {
            rows.forEachIndexed { index, cells ->
                Row(Modifier.height(IntrinsicSize.Min)) {
                    repeat(columns) { column ->
                        Box(Modifier.width(cellWidth).fillMaxHeight()
                            .background(if (index == 0) MaterialTheme.colorScheme.surfaceVariant else MaterialTheme.colorScheme.surface)
                            .border(0.5.dp, MaterialTheme.colorScheme.outlineVariant).padding(8.dp)) {
                            Markdown(content = cells.getOrNull(column)?.getTextInNode(model.content)?.toString()?.trim().orEmpty(),
                                modifier = Modifier.fillMaxWidth(),
                                typography = replyTypography(tableHeader = index == 0),
                                padding = markdownPadding(block = 0.dp, list = 0.dp, listItemBottom = 4.dp, indentList = 12.dp))
                        }
                    }
                }
            }
        }
    }
}
