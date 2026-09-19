package com.maoer.lite.data.manager

object PlaybackOptions {
    val speeds = listOf(0.75f, 1f, 1.25f, 1.5f, 1.75f, 2f)
    fun validSpeed(speed: Float) = speed.takeIf { it in speeds } ?: 1f
}

data class SleepTimerState(val remainingMs: Long = 0, val endOfEpisode: Boolean = false) {
    val active: Boolean get() = remainingMs > 0 || endOfEpisode
}

/** Uses a monotonic platform clock. Pausing playback does not pause the countdown. */
class SleepTimerPolicy {
    private var deadlineMs: Long? = null
    private var endOfEpisode = false

    fun afterMinutes(minutes: Int, nowMs: Long) {
        require(minutes in 1..180)
        deadlineMs = nowMs + minutes * 60_000L
        endOfEpisode = false
    }

    fun atEpisodeEnd() { deadlineMs = null; endOfEpisode = true }
    fun cancel() { deadlineMs = null; endOfEpisode = false }
    fun state(nowMs: Long) = SleepTimerState(
        remainingMs = deadlineMs?.let { (it - nowMs).coerceAtLeast(0) } ?: 0,
        endOfEpisode = endOfEpisode,
    )
    fun consumeExpiry(nowMs: Long): Boolean {
        if (deadlineMs?.let { nowMs >= it } != true) return false
        cancel()
        return true
    }
}
