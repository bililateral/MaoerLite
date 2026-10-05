"""Shared task/checkpoint contract. Persisted keys and budgets remain stable."""
from typing import TypedDict

TERMINAL = {'completed', 'cancelled', 'failed'}
MAX_TOOL_CALLS = 24
MAX_MODEL_ROUNDS = MAX_TOOL_CALLS + 1
READ_ONLY = {'search_catalog', 'list_episodes', 'get_playback_state', 'get_podcast_details', 'search_podcast_content'}


class State(TypedDict):
    run_id: str
    messages: list[dict]
    rounds: int
    calls: list[dict]
    memory: dict
    tool_count: int
    final_only: bool
    summary_candidates: list[str]
    summary: dict
    summary_pending: int
    extended_episodes: bool
    deferred_results: list[dict]



class TaskConflict(Exception):
    pass
