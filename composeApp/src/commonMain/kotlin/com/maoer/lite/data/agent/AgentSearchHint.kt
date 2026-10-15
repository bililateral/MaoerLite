package com.maoer.lite.data.agent

import com.maoer.lite.data.podcast.BuiltInPodcasts
import com.maoer.lite.data.podcast.PodcastSearchIndex

/** Only exact catalog titles/categories may become passive placeholders. */
fun agentSearchHint(state: AgentUiState): String {
    if (!state.ready || !state.personalizedSearch || state.history.deletePending || state.history.turns.isEmpty()) return ""
    val last = state.history.turns.lastOrNull { it.run?.status == "completed" }?.run ?: return ""
    val hint = last.search_hint.trim()
    val sources = BuiltInPodcasts.sources
    return hint.takeIf { it.isNotBlank() && sources.any { source -> source.title == it || source.category == it } &&
        PodcastSearchIndex(sources).search(it).isNotEmpty() }.orEmpty()
}
