package com.maoer.lite.data.podcast

import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.prepareGet
import io.ktor.client.statement.bodyAsChannel
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.utils.io.core.readBytes
import io.ktor.utils.io.readRemaining
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit

class PodcastRepository(
    private val client: HttpClient,
    private val cache: PodcastCache,
    private val parser: RssParser,
) {
    val sources = BuiltInPodcasts.sources
    private val locks = sources.associate { it.id to Mutex() }
    private val requests = Semaphore(3)
    suspend fun cached(id: String): PodcastFeed? = cache.read(id)?.feed
    fun source(id: String): PodcastSource = sources.first { it.id == id }

    suspend fun refresh(id: String): PodcastFeed = locks.getValue(id).withLock {
        requests.withPermit {
            val source = source(id)
            val previous = cache.read(id)
            client.prepareGet(source.feedUrl) {
                header(HttpHeaders.UserAgent, "MaoerLite/0.2 (podcast RSS reader)")
                // This client also serves JSON APIs. RSS endpoints can reject a JSON-only Accept with 406.
                header(HttpHeaders.Accept, "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8")
                previous?.etag?.let { header(HttpHeaders.IfNoneMatch, it) }
                previous?.modified?.let { header(HttpHeaders.IfModifiedSince, it) }
            }.execute { response ->
                if (response.status == HttpStatusCode.NotModified && previous != null) return@execute previous.feed
                check(response.status.value in 200..299) { "节目源暂不可用（HTTP ${response.status.value}）" }
                val bytes = response.bodyAsChannel().readRemaining(8_000_001).readBytes()
                check(bytes.size <= 8_000_000) { "节目源超过 8 MB，请稍后重试" }
                val feed = withContext(Dispatchers.Default) { parser.parse(bytes.decodeToString(), source) }
                cache.write(id, CachedFeed(feed, response.headers[HttpHeaders.ETag], response.headers[HttpHeaders.LastModified]))
                feed
            }
        }
    }
}
