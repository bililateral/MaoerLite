package com.maoer.lite.data.manager

import com.maoer.lite.data.local.KeyValueStorage
import com.maoer.lite.data.model.Audio
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

@Serializable
data class PlaybackSnapshot(val queue: List<Audio>, val currentId: String, val positionMs: Long, val speed: Float = 1f)

/** Shared on-disk format for UI restoration and background-service snapshots. */
class PlaybackSnapshotStore(private val storage: KeyValueStorage) {
    private val reader = Json { ignoreUnknownKeys = true }
    suspend fun load(): PlaybackSnapshot? = storage.getString(KEY)?.let { reader.decodeFromString(it) }
    suspend fun save(snapshot: PlaybackSnapshot) = storage.saveString(KEY, Json.encodeToString(snapshot))
    companion object { const val KEY = "playback_snapshot_v2" }
}
