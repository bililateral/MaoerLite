"""Intent data shared by route selection and workflows."""
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from typing import Literal
from maoer_agent.core.limits import MAX_TOOL_CALLS
from maoer_agent.contracts.tasks import ControlAction,UnsupportedAction,TaskPlan

class ControlIntent(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    actions: list[ControlAction] = Field(default_factory=list, max_length=MAX_TOOL_CALLS)


class NetworkConstraint(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    restriction: Literal['none', 'forbidden']
    source_quote: str = Field(default='', max_length=300)


class WebDelivery(BaseModel):
    """Independent answer and link obligations; results-only excludes a factual answer."""
    model_config = ConfigDict(strict=True, extra='forbid')
    factual_answer: bool = True
    source_links: bool = False
    results_only: bool = False

    @model_validator(mode='after')
    def consistent(self):
        if self.results_only and self.factual_answer:
            raise ValueError('Results-only and a factual answer are distinct delivery requests')
        return self


class WebLookup(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    query: str = Field(default='', max_length=300)
    delivery: Literal['answer', 'results'] = 'answer'
    requirements: WebDelivery | None = None


def web_requirements(route):
    lookup = route.get('web_lookup', {})
    if lookup.get('requirements') is not None:
        return lookup['requirements']
    # Existing checkpoints predate the independent delivery dimensions.
    results = lookup.get('delivery') == 'results'
    return {'factual_answer': not results, 'source_links': results, 'results_only': results}


def web_delivery(route):
    needs = web_requirements(route)
    return 'results' if needs['results_only'] and not needs['factual_answer'] else 'answer'


def web_source_links(route):
    return web_requirements(route)['source_links'] or web_delivery(route) == 'results'


def episode_description_required(route, goal):
    return goal.get('kind') == 'catalog_query' and route.get('response_needs', {}).get('brief_episode_intro', False)


class ResponseNeeds(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    app_guidance: bool = False
    acknowledgement_only: bool = False
    retrieval_statistics: bool = False
    brief_episode_intro: bool = False
    library_listing: bool = False
    library_completion: Literal['all', 'completed', 'incomplete'] = 'all'
    library_scope: Literal['all', 'favorites'] = 'all'
    library_kind: Literal['all', 'podcasts', 'episodes'] = 'all'
    library_scope_quote: str = Field(default='', max_length=300)
    last_library_page: bool = False
    titles_only: bool = False
    episode_page_listing: bool = False
    control_explanation: Literal['none', 'cancel_sleep_timer'] = 'none'


class UnsupportedRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    actions: list[UnsupportedAction] = Field(default_factory=list, max_length=MAX_TOOL_CALLS)
    exclusive: bool = False


class PageLookup(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    requested: bool = False
    target_kind: Literal['episode_page', 'episode_transcript', 'podcast_homepage', 'evidence_quote', 'episode_selection', 'none'] = 'episode_page'
    selector_kind: Literal['none', 'reverse_source', 'source', 'newest', 'title_number', 'displayed_page'] = 'none'
    selector_value: int = Field(default=0, ge=0, le=100000)
    page_reference: str = Field(default='', max_length=40)
    exclusive: bool = False
    podcast_title: str = Field(default='', max_length=150)
    episode_title: str = Field(default='', max_length=300)
    query: str = Field(default='', max_length=300)
    other_tasks: list[Literal['episode_analysis', 'recommendation', 'other_web_query', 'general_question']] = Field(default_factory=list, max_length=8)


class EpisodeCriteria(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    requested: bool = False
    podcast_title: str = Field(default='', max_length=150)
    topic: str = Field(default='', max_length=300)
    max_duration_seconds: int = Field(default=0, ge=0)
    preference: str = Field(default='', max_length=150)
    delivery: Literal['candidates','analysis','all_titles'] = 'analysis'
    match_mode: Literal['semantic','literal'] = 'semantic'
    required_terms: list[str] = Field(default_factory=list, max_length=8)
    excluded_terms: list[str] = Field(default_factory=list, max_length=8)

    @field_validator('required_terms','excluded_terms')
    @classmethod
    def literal_terms(cls, values):
        if any(not 1 <= len(value.strip()) <= 30 for value in values):
            raise ValueError('Use literal terms of 1..30 characters')
        return list(dict.fromkeys(value.strip() for value in values))

    @model_validator(mode='after')
    def literal_listing(self):
        if self.delivery == 'all_titles' and (self.match_mode != 'literal' or not self.requested
                or not self.podcast_title.strip() or not (self.required_terms or self.excluded_terms)):
            raise ValueError('An exhaustive literal listing needs its podcast and literal predicates')
        return self
