"""Intent data shared by route selection and workflows."""
from pydantic import BaseModel, ConfigDict, Field


class PageLookup(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    requested: bool = False
    exclusive: bool = False
    podcast_title: str = Field(default='', max_length=150)
    episode_title: str = Field(default='', max_length=300)
    query: str = Field(default='', max_length=300)
    other_tasks: list[str] = Field(default_factory=list, max_length=8)
