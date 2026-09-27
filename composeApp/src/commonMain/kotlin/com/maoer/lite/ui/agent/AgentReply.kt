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
import com.mikepenz.markdown.compose.components.MarkdownComponentModel
import com.mikepenz.markdown.compose.components.markdownComponents
import com.mikepenz.markdown.m3.Markdown
import org.intellij.markdown.ast.getTextInNode
import org.intellij.markdown.flavours.gfm.GFMElementTypes
import org.intellij.markdown.flavours.gfm.GFMTokenTypes

/** Render model text only: no remote image loader, HTML execution or tool actions. */
@Composable
internal fun AgentReply(content: String, modifier: Modifier = Modifier) {
    SelectionContainer {
        Markdown(content = content, modifier = modifier, components = markdownComponents(
            custom = { type, model ->
                if (type == GFMElementTypes.TABLE) ReplyTable(model)
                else Text(model.node.getTextInNode(model.content).toString())
            }
        ))
    }
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
                                modifier = Modifier.fillMaxWidth())
                        }
                    }
                }
            }
        }
    }
}
