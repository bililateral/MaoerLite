"""Intent data shared by route selection and workflows."""
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from maoer_agent.core.limits import MAX_TOOL_CALLS

ControlAction = Literal['play_episode', 'pause_playback', 'resume_playback', 'next_episode',
                        'previous_episode', 'set_playback_speed', 'set_sleep_timer']


class ControlIntent(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    actions: list[ControlAction] = Field(default_factory=list, max_length=MAX_TOOL_CALLS)


class PageLookup(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    requested: bool = False
    target_kind: Literal['episode_page', 'podcast_homepage', 'evidence_quote', 'episode_selection', 'none'] = 'episode_page'
    selector_kind: Literal['none', 'reverse_source', 'source', 'newest', 'title_number'] = 'none'
    selector_value: int = Field(default=0, ge=0, le=100000)
    exclusive: bool = False
    podcast_title: str = Field(default='', max_length=150)
    episode_title: str = Field(default='', max_length=300)
    query: str = Field(default='', max_length=300)
    other_tasks: list[str] = Field(default_factory=list, max_length=8)


class EpisodeCriteria(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    requested: bool = False
    podcast_title: str = Field(default='', max_length=150)
    topic: str = Field(default='', max_length=300)
    max_duration_seconds: int = Field(default=0, ge=0)
    preference: str = Field(default='', max_length=150)
