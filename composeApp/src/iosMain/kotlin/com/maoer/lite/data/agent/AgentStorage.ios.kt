package com.maoer.lite.data.agent

import com.maoer.lite.data.local.getDataStorePath
import okio.Path.Companion.toPath

actual fun agentStoragePath(): String = (getDataStorePath().toPath().parent!! / "agent").toString()
// Android-only delivery. Never claim an unparsed iOS date is a verified latest episode.
actual fun agentPublishedMillis(value: String): Long? = null
