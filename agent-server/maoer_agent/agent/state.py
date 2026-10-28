"""Shared task/checkpoint contract; existing records and budgets stay compatible."""
from typing import TypedDict

TERMINAL = {'completed', 'cancelled', 'failed'}
from maoer_agent.core.limits import MAX_TOOL_CALLS, MAX_MODEL_ROUNDS
from maoer_agent.tools.definitions import READ_ONLY


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
    summary_covered: list[str]
    history_turn_ids: list[str]
    extended_episodes: bool
    deferred_results: list[dict]
    research: bool
    route: dict
    research_plan: list[dict]
    route_rechecked: bool
    identity_repairs: int
    continue_model: bool



class TaskConflict(Exception):
    pass
