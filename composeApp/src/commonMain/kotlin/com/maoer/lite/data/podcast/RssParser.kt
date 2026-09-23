package com.maoer.lite.data.podcast

import nl.adaptivity.xmlutil.EventType
import nl.adaptivity.xmlutil.xmlStreaming
import okio.ByteString.Companion.encodeUtf8

/** RSS semantics stay in commonMain. The generic XML reader is shared across platforms. */
class RssParser {
    private data class Node(val name: String, val ns: String, val value: StringBuilder = StringBuilder())
    private class Entry {
        var guid = ""; var title = ""; var url = ""; var description = ""
        var cover = ""; var published = ""; var duration = 0L
    }

    fun parse(xml: String, source: PodcastSource): PodcastFeed {
        require(xml.length <= 8_000_000) { "节目源过大，暂不支持" }
        require(!xml.contains("<!DOCTYPE", true) && !xml.contains("<!ENTITY", true)) { "不支持带实体声明的节目源" }
        val reader = xmlStreaming.newGenericReader(xml.removePrefix("\uFEFF"))
        val stack = mutableListOf<Node>()
        val entries = mutableListOf<Entry>()
        var rawItems = 0; var rejectedItems = 0; var cappedItems = 0
        var entry: Entry? = null
        var title = source.title; var author = ""; var description = ""; var cover = source.coverUrl
        var channelFound = false
        try {
            while (reader.hasNext()) {
                when (reader.next()) {
                    EventType.START_ELEMENT -> {
                        require(stack.size < 64) { "节目源结构过深" }
                        val node = Node(reader.localName, reader.namespaceURI)
                        val parent = stack.lastOrNull()?.name
                        stack.add(node)
                        if (node.name == "channel" && parent == "rss") channelFound = true
                        if (node.name == "item" && parent == "channel") { entry = Entry(); rawItems++ }
                        if (node.name == "enclosure" && parent == "item") {
                            val mime = reader.getAttributeValue(null, "type").orEmpty()
                            if (mime.isBlank() || mime.startsWith("audio/") || mime == "application/octet-stream") {
                                entry?.url = safeUrl(reader.getAttributeValue(null, "url").orEmpty())
                            }
                        }
                        if (node.name == "image" && node.ns == ITUNES) {
                            val url = safeUrl(reader.getAttributeValue(null, "href").orEmpty())
                            if (parent == "item") entry?.cover = url
                            else if (parent == "channel" && url.isNotBlank()) cover = url
                        }
                    }
                    EventType.TEXT, EventType.CDSECT, EventType.ENTITY_REF -> {
                        stack.lastOrNull()?.let { node ->
                            if (node.value.length < 32_768) node.value.append(reader.text.take(32_768 - node.value.length))
                        }
                    }
                    EventType.END_ELEMENT -> {
                        val node = stack.removeAt(stack.lastIndex)
                        val parent = stack.lastOrNull()
                        val value = node.value.toString().trim()
                        if (parent?.name == "item") {
                            entry?.let { e -> when (node.name) {
                                "guid" -> e.guid = value
                                "title" -> e.title = plainText(value)
                                "description" -> if (e.description.isBlank()) e.description = plainText(value)
                                "encoded" -> if (node.ns == CONTENT) e.description = plainText(value)
                                "pubDate" -> e.published = value
                                "duration" -> if (node.ns == ITUNES) e.duration = parseDuration(value)
                            } }
                        } else if (parent?.name == "channel") {
                            when (node.name) {
                                "title" -> if (value.isNotBlank()) title = plainText(value)
                                "author" -> if (node.ns == ITUNES) author = plainText(value)
                                "description" -> description = plainText(value)
                            }
                        } else if (parent?.name == "image" && node.name == "url" && stack.getOrNull(stack.lastIndex - 1)?.name == "channel") {
                            safeUrl(value).takeIf { it.isNotBlank() }?.let { cover = it }
                        }
                        if (node.name == "item" && parent?.name == "channel") {
                            entry?.let {
                                if (it.url.isBlank() || it.title.isBlank()) rejectedItems++
                                else if (entries.size < 1000) entries.add(it)
                                else cappedItems++
                            }
                            entry = null
                        } else if (parent != null && parent.value.length < 32_768) {
                            parent.value.append(" ").append(value.take(32_768 - parent.value.length))
                        }
                    }
                    EventType.DOCDECL -> error("不支持 XML 实体声明")
                    else -> Unit
                }
            }
        } finally { reader.close() }
        require(channelFound) { "地址未返回有效 RSS 节目" }
        val episodes = entries.map { e ->
            // GUID is stable across URL changes. For sources without GUID, use content identity
            // when a publication date exists; URL is the final fallback, never list position.
            val identity = e.guid.ifBlank { if (e.published.isNotBlank()) "${e.title}|${e.published}" else e.url }
            PodcastEpisode("${source.id}:${identity.encodeUtf8().sha256().hex()}", e.title, e.url,
                e.description, e.cover, e.published, e.duration)
        }.distinctBy { it.id }
        require(episodes.isNotEmpty()) { "节目源中没有可播放的音频分集" }
        return PodcastFeed(source.id, title, author, description, cover, episodes,
            FeedDiagnostics(rawItems, rejectedItems, cappedItems, entries.size - episodes.size))
    }

    companion object {
        private const val ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
        private const val CONTENT = "http://purl.org/rss/1.0/modules/content/"
        fun parseDuration(value: String): Long {
            val parts = value.trim().split(':')
            if (parts.size !in 1..3) return 0
            val numbers = parts.map { it.toLongOrNull() ?: return 0 }
            if (numbers.any { it < 0 } || numbers.any { it > 10_000_000 }) return 0
            return numbers.fold(0L) { total, part -> total * 60 + part }
        }
        fun safeUrl(value: String): String = value.trim().takeIf {
            it.startsWith("https://", true) || it.startsWith("http://", true)
        }.orEmpty()
        fun plainText(value: String): String = value
            .replace(Regex("<[^>]*>"), " ")
            .replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&quot;", "\"").replace("&#39;", "'")
            .replace("&lt;", "<").replace("&gt;", ">")
            .replace(Regex("\\s+"), " ").trim().take(16_384)
    }
}
