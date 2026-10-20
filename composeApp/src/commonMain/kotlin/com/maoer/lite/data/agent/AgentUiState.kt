package com.maoer.lite.data.agent

data class AgentUiState(val history: AgentHistory = AgentHistory(), val ready: Boolean = false,
                        val connected: Boolean = false, val working: Boolean = false, val notice: String = "",
                        val personalizedSearch: Boolean = true)
