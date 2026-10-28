"""User goals and dependency edges, independent of tools, APIs and storage."""
from typing import Literal, get_args
from pydantic import BaseModel,ConfigDict,Field
import re

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
    podcast_id: str = Field(default='', max_length=150)
    episode_id: str = Field(default='', max_length=2000)
    query: str = Field(default='', max_length=300)
    other_tasks: list[Literal['episode_analysis', 'recommendation', 'other_web_query', 'general_question']] = Field(default_factory=list, max_length=8)


class GoalDelivery(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    exact_text: str = Field(default='', max_length=2000)
    sentence_count: int = Field(default=0, ge=0)
    titles_only: bool | None = None
    source_quote: str = Field(default='', max_length=500)


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
    target: PageLookup | None = None
    delivery: GoalDelivery = Field(default_factory=GoalDelivery)


class TaskPlan(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    goals: list[TaskGoal]=Field(default_factory=list,max_length=36)

    def checked(self,actions,question=None):
        value=self.model_dump()
        if question is not None and not self.goals:
            raise ValueError('Current user request needs a goal, including acknowledgement and its explicit delivery requirements')
        by_id={g['id']:g for g in value['goals']}
        if len(by_id)!=len(self.goals):raise ValueError('Duplicate goal identity')
        if [g.action for g in self.goals if g.kind=='control']!=list(actions):
            raise ValueError('Goals must preserve declared control actions, order and counts')
        previous=None
        for goal in value['goals']:
            delivery=goal['delivery']
            if question is not None and (delivery['exact_text'] or delivery['sentence_count']):
                quote=delivery['source_quote']
                if not quote or quote not in question or (delivery['exact_text'] and delivery['exact_text'] not in quote):
                    raise ValueError('Explicit delivery constraints need their current user quote')
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


def reply_requirements(route):
    return [{'goal_id':g['id'], **g['delivery']} for g in route.get('task_plan',{}).get('goals',[])
            if g.get('delivery') and (g['delivery'].get('exact_text') or g['delivery'].get('sentence_count')
                                     or g['delivery'].get('titles_only') is not None)]


def checked_reply(text, route):
    """Whole-reply constraints belong to a single user goal; never trim prose."""
    goals=route.get('task_plan',{}).get('goals',[])
    if len(goals)!=1:return text
    spec=goals[0].get('delivery') or {}
    if spec.get('exact_text') and text!=spec['exact_text']:
        raise ValueError('Reply must equal the explicit user text')
    count=spec.get('sentence_count',0)
    if count:
        prose=re.sub(r'\[[0-9]+\]', '', text).strip()
        endings=list(re.finditer(r'[。！？!?]+|(?<![0-9])\.(?=\s|$)',prose))
        actual=len(endings)+bool(prose[endings[-1].end() if endings else 0:].strip(' \n\r\t”’"\''))
        if actual!=count:raise ValueError(f'Reply needs {count} sentences; found {actual}')
    return text
