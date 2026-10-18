"""Shared task/checkpoint contract. Persisted keys and budgets remain stable."""
from typing import TypedDict

TERMINAL = {'completed', 'cancelled', 'failed'}
MAX_TOOL_CALLS = 24
MAX_MODEL_ROUNDS = MAX_TOOL_CALLS + 1
from agent_tools import READ_ONLY


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
    research: bool
    route: dict
    research_plan: list[dict]
    route_rechecked: bool
    identity_repairs: int



class TaskConflict(Exception):
    pass
