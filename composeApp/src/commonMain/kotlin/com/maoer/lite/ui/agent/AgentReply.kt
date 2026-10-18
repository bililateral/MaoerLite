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
import androidx.compose.ui.text.withStyle
import androidx.compose.runtime.remember
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import com.mikepenz.markdown.compose.components.MarkdownComponentModel
import com.mikepenz.markdown.compose.components.markdownComponents
import com.mikepenz.markdown.m3.Markdown
import com.mikepenz.markdown.m3.markdownTypography
import com.mikepenz.markdown.model.markdownPadding
import com.mikepenz.markdown.model.markdownAnnotator
import org.intellij.markdown.MarkdownElementTypes
import org.intellij.markdown.ast.ASTNode
import org.intellij.markdown.ast.getTextInNode
import org.intellij.markdown.flavours.gfm.GFMFlavourDescriptor
import org.intellij.markdown.flavours.gfm.GFMElementTypes
import org.intellij.markdown.flavours.gfm.GFMTokenTypes
import org.intellij.markdown.parser.MarkdownParser

/** Render model text only: no remote image loader, HTML execution or tool actions. */
@Composable
internal fun AgentReply(content: String, modifier: Modifier = Modifier, citations: Set<Int> = emptySet(), onCitation: (Int) -> Unit = {}) {
    val original = LocalUriHandler.current
    val rendered = remember(content, citations) {
        replyCitationLinks(content, citations)
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
            annotator = replyAnnotator(),
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

/** A citation is prose; code, existing links and images must retain their literal input. */
internal fun replyCitationLinks(content: String, citations: Set<Int>): String {
    if (citations.isEmpty()) return content
    val protected = mutableListOf<IntRange>()
    val tree = MarkdownParser(GFMFlavourDescriptor()).buildMarkdownTreeFromString(content)
    fun label(node: ASTNode) = node.getTextInNode(content).toString().trim().lowercase().replace(Regex("\\s+"), " ")
    val definitions = mutableSetOf<String>()
    fun collectDefinitions(node: ASTNode) {
        if (node.type == MarkdownElementTypes.LINK_DEFINITION)
            node.children.firstOrNull { it.type == MarkdownElementTypes.LINK_LABEL }?.let { definitions += label(it) }
        else node.children.forEach(::collectDefinitions)
    }
    collectDefinitions(tree)
    val literalTypes = setOf(MarkdownElementTypes.CODE_SPAN, MarkdownElementTypes.CODE_FENCE,
        MarkdownElementTypes.CODE_BLOCK, MarkdownElementTypes.INLINE_LINK,
        MarkdownElementTypes.FULL_REFERENCE_LINK, MarkdownElementTypes.LINK_DEFINITION,
        MarkdownElementTypes.IMAGE, GFMTokenTypes.GFM_AUTOLINK)
    fun collect(node: ASTNode) {
        if (node.type in literalTypes || (node.type == MarkdownElementTypes.SHORT_REFERENCE_LINK && label(node) in definitions)) {
            protected += node.startOffset until node.endOffset
        } else node.children.forEach(::collect)
    }
    collect(tree)
    return Regex("(?<!!)\\[(\\d{1,3})\\](?!\\()").replace(content) { match ->
        val number = match.groupValues[1].toInt()
        if (number in citations && protected.none { match.range.first <= it.last && match.range.last >= it.first })
            "[${match.value}](maoer-source://$number)" else match.value
    }
}

/** Code spans are literal text, including XML tags, links and Markdown punctuation. */
internal fun replyCodeText(content: String, node: ASTNode): String {
    if (node.children.size < 2) return node.getTextInNode(content).toString()
    val text = content.substring(node.children.first().endOffset, node.children.last().startOffset)
        .replace("\r\n", " ").replace('\n', ' ').replace('\r', ' ')
    return if (text.startsWith(' ') && text.endsWith(' ') && text.any { it != ' ' }) text.drop(1).dropLast(1) else text
}

@Composable
private fun replyAnnotator() = replyTypography().code.toSpanStyle().let { style ->
    markdownAnnotator { content, node ->
        if (node.type == MarkdownElementTypes.CODE_SPAN) {
            withStyle(style) { append(replyCodeText(content, node)) }
            true
        } else false
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
                                annotator = replyAnnotator(),
                                padding = markdownPadding(block = 0.dp, list = 0.dp, listItemBottom = 4.dp, indentList = 12.dp))
                        }
                    }
                }
            }
        }
    }
}
