"""User goals and dependency edges, independent of tools, APIs and storage."""
from typing import Literal, get_args
from pydantic import BaseModel,ConfigDict,Field

ControlAction = Literal['play_episode','pause_playback','resume_playback','next_episode',
                        'previous_episode','set_playback_speed','set_sleep_timer']
UnsupportedAction = Literal['add_favorite','remove_favorite','mark_completed','download','delete_download']


class CatalogPageRequest(BaseModel):
    """One requested catalog page, relative to an already delivered list."""
    model_config=ConfigDict(strict=True,extra='forbid')
    operation: Literal['first','next','offset']
    reference: str=Field(default='',max_length=40)
    offset: int=Field(default=0,ge=0,le=100000)
    podcast_title: str=Field(default='',max_length=150)
    count: int=Field(default=0,ge=0,description='Explicit number of episodes to display; 0 means one tool page')


class TaskGoal(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    id: str=Field(min_length=1,max_length=40)
    kind: Literal['control','device_query','public_query','catalog_query','library_query','conversation_query','general','unsupported']
    request: str=Field(min_length=1,max_length=500)
    depends_on: list[str]=Field(default_factory=list,max_length=36)
    require_success: bool=False
    action: ControlAction | UnsupportedAction | Literal['']=''
    arguments: dict=Field(default_factory=dict)
    query: str=Field(default='',max_length=300)
    as_of: Literal['current','historical','general']='general'
    requested_results: int = Field(default=0,ge=0,le=48,description='Explicit number of search result links requested for this goal; 0 when unspecified')
    page_request: CatalogPageRequest | None = None


class TaskPlan(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    goals: list[TaskGoal]=Field(default_factory=list,max_length=36)

    def checked(self,actions):
        value=self.model_dump()
        by_id={g['id']:g for g in value['goals']}
        if len(by_id)!=len(self.goals):raise ValueError('Duplicate goal identity')
        if [g.action for g in self.goals if g.kind=='control']!=list(actions):
            raise ValueError('Goals must preserve declared control actions, order and counts')
        previous=None
        for goal in value['goals']:
            if goal['kind']=='control':
                if previous and previous not in goal['depends_on']:goal['depends_on'].append(previous)
                previous=goal['id']
        visited,active=set(),set()
        def visit(identity):
            if identity in active or identity not in by_id:raise ValueError('Invalid goal dependency')
            if identity in visited:return
            active.add(identity)
            for dependency in by_id[identity]['depends_on']:visit(dependency)
            active.remove(identity);visited.add(identity)
        for goal in value['goals']:
            if goal['kind']=='control':
                if goal['action'] not in get_args(ControlAction):raise ValueError('Controls require a supported control action')
            elif goal['kind']=='unsupported':
                if goal['action'] and goal['action'] not in get_args(UnsupportedAction):raise ValueError('Unsupported goals cannot carry executable controls')
            elif goal['action']:raise ValueError('Queries cannot carry actions')
            if goal['page_request'] is not None and goal['kind']!='catalog_query':
                raise ValueError('Only a catalog query can request a catalog page')
            visit(goal['id'])
        return value
