"""Narrow relevance and model-call contracts without runtime or API objects."""
from typing import Awaitable, Callable, Protocol
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

RelevanceCall = Callable[[list[dict]], Awaitable[dict]]


class RelevanceJudge(Protocol):
    namespace: str
    async def select(self, query: str, candidates: list[dict], model: RelevanceCall | None) -> list[dict]: ...


class WebMatch(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    id: str
    relation: Literal['primary', 'section', 'mention', 'unrelated']
    passage: str = ''
    quote: str = Field(default='', max_length=1600)


class WebSelection(BaseModel):
    items: list[dict] = Field(default_factory=list)
    rejected: list[dict] = Field(default_factory=list)
