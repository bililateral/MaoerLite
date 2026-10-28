"""Conversation data shared by model purposes, independent of runtime and APIs."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ConversationContext:
    current_request: str
    history: list[dict]
    dialogue: list[dict]
    memory: dict
    summary: dict
    summary_pending: int
    user_statements: dict[str, str]
    known_materials: list[dict]
    episode_pages: list[dict]
    displayed_episode_pages: list[dict]
    retained_turn_ids: list[str]
    summary_covered_turn_ids: list[str]
