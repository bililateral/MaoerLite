package com.maoer.lite.data.manager

/** List repeat is a product requirement shared by page and service controls. */
object QueueNavigation {
    fun nextIndex(current: Int, size: Int): Int =
        if (size <= 0 || current !in 0 until size) -1 else (current + 1) % size
    fun previousIndex(current: Int, size: Int): Int =
        if (size <= 0 || current !in 0 until size) -1 else (current + size - 1) % size
}
