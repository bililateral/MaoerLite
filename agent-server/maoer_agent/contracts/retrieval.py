"""Narrow relevance and model-call contracts without runtime or API objects."""
from typing import Awaitable, Callable, Protocol

RelevanceCall = Callable[[list[dict]], Awaitable[dict]]


class RelevanceJudge(Protocol):
    namespace: str
    async def select(self, query: str, candidates: list[dict], model: RelevanceCall | None) -> list[dict]: ...
