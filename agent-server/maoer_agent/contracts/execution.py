"""Data passed to tools and workflows, independent of runtime persistence."""
from dataclasses import dataclass
from maoer_agent.contracts.retrieval import RelevanceCall


@dataclass(frozen=True)
class ResearchContext:
    request_key: str
    question: str
    sources: tuple[dict, ...]
    relevance_model: RelevanceCall | None = None
    as_of_date: str = ''
    known_urls: tuple[str, ...] = ()
    web_relevance_model: RelevanceCall | None = None
    information_request: str = ''


@dataclass(frozen=True)
class PageView:
    route: dict
    messages: list[dict]
    checks: dict
    sources: list[dict]
    feed_urls: frozenset[str]
