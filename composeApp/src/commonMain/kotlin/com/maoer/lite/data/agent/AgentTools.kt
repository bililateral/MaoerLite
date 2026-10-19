package com.maoer.lite.data.agent

import com.maoer.lite.data.manager.PlayerManager
import com.maoer.lite.data.podcast.PodcastRepository
import com.maoer.lite.data.library.ListeningLibrary
import com.maoer.lite.data.download.Downloads

/** Stable session-facing facade; execution durability is separate from business tools. */
class AgentTools(repository: PodcastRepository, player: PlayerManager, storage: AgentStorage, api: AgentApi, library: ListeningLibrary, downloads: Downloads) {
    private val state = PlaybackStateReader(repository, player)
    private val catalog = CatalogToolHandler(repository)
    private val playback = PlaybackToolHandler(repository, player, state)
    private val personal = LibraryToolHandler(repository, library, downloads)
    private val executor = AgentToolExecutor(storage,
        claim = { run, call -> api.claim(run, call); Unit },
        perform = { call -> when {
            call.name == "query_library" -> personal.perform(call)
            call.name in CatalogToolHandler.names -> catalog.perform(call)
            else -> playback.perform(call)
        } },
        snapshot = state::read)

    suspend fun execute(run: String, call: AgentCall, conversationId: String = ""): AgentReceipt =
        executor.execute(run, call, conversationId)

    suspend fun unsubmittedReceipts(run: AgentRun): List<AgentReceipt> = executor.unsubmittedReceipts(run)
}
