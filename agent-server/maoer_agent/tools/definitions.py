"""Only these typed tools may be dispatched to the Android application."""
import json
from dataclasses import dataclass
from typing import Literal
from pydantic import Field, field_validator, model_validator
from maoer_agent.api.protocol import StrictModel, ToolCall


class Search(StrictModel):
    query: str = Field(min_length=1, max_length=100)


class LegacyEpisodes(StrictModel):
    podcast_id: str = Field(min_length=1, max_length=150)
    query: str = Field(default='', max_length=100)


class Episodes(LegacyEpisodes):
    order: Literal['newest', 'source', 'reverse_source'] = 'newest'
    offset: int = Field(default=0, ge=0, le=100000)
    position: int = Field(default=0, ge=0, le=100000, description='指定排序中从1开始的完整列表位置，0表示不按位置选择；不是标题期号')
    episode_number: int = Field(default=0, ge=0, le=100000, description='标题开头的节目期号，如158.或EP158；0表示不按期号选择')

    @model_validator(mode='after')
    def one_selector(self):
        if sum((bool(self.query.strip()), self.position > 0, self.episode_number > 0)) > 1 or (self.position and self.offset):
            raise ValueError('Use a single episode selector; position cannot use offset')
        return self


class Play(StrictModel):
    podcast_id: str = Field(min_length=1, max_length=150)
    episode_id: str = Field(min_length=1, max_length=2000)


class PodcastDetails(StrictModel):
    podcast_id: str = Field(min_length=1, max_length=150)


class ContentSearch(StrictModel):
    keywords: list[str] = Field(min_length=1, max_length=4)
    podcast_id: str = Field(default='', max_length=150)

    @field_validator('keywords')
    @classmethod
    def keyword_lengths(cls, values):
        if any(not 2 <= len(value.strip()) <= 30 for value in values):
            raise ValueError('Use 1..4 short keywords of 2..30 characters')
        return [value.strip() for value in values]


class Empty(StrictModel):
    pass


class ResearchSearch(StrictModel):
    query: str = Field(min_length=2, max_length=300)
    podcast_id: str = Field(default='', max_length=150)
    required_terms: list[str] = Field(default_factory=list, max_length=8, description='可选全文字面筛选：分集说明必须包含每个词，不是语义匹配。')
    excluded_terms: list[str] = Field(default_factory=list, max_length=8, description='可选全文字面排除：说明含任一词即排除；截断说明不能证明不含，亦不作为排除后的候选。不能据此保证音频不含。')

    @field_validator('required_terms', 'excluded_terms')
    @classmethod
    def literal_terms(cls, values):
        if any(not 1 <= len(value.strip()) <= 30 for value in values):
            raise ValueError('Use literal terms of 1..30 characters')
        return list(dict.fromkeys(value.strip() for value in values))


class WebSearch(StrictModel):
    query: str = Field(min_length=2, max_length=300)


class WebRead(StrictModel):
    url: str = Field(min_length=8, max_length=2000)
    query: str = Field(default='', max_length=100)
    offset: int = Field(default=0, ge=0, le=200000)


class EpisodeRead(Play):
    offset: int = Field(default=0, ge=0, le=262144)


class Timer(StrictModel):
    minutes: int = Field(ge=-1, le=180, description='1..180 定时分钟；0 取消；-1 本集播完停止')


class Speed(StrictModel):
    speed: Literal[0.75, 1.0, 1.25, 1.5, 1.75, 2.0]

    @field_validator('speed', mode='before')
    @classmethod
    def numeric_speed(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('Speed must be a number')
        return value


@dataclass(frozen=True)
class ToolSpec:
    input_model: type
    description: str
    location: str
    scope: str
    result_kind: str
    changes_state: bool = False
    capability: str = ''
    retry: str = 'explicit_read'
    retention: str = 'receipt'


TOOLS = {
    'web_search': ToolSpec(WebSearch, '搜索公开互联网以核实最新外部信息。仅免费额度，失败不自动付费。返回摘要及真实网址；关键事实应继续read_web_page读取原文。不是播客检索。', 'server', 'web', 'web', changes_state=False, capability='research_v3', retry='reserved_credit', retention='document'),
    'read_web_page': ToolSpec(WebRead, '读取本轮搜索结果或用户提供的公开网页正文。query可定位文中词，offset按字符分页；只有实际返回原文能作为引用依据。', 'server', 'web', 'web', changes_state=False, capability='research_v3', retry='reserved_credit', retention='document'),
    'search_rss': ToolSpec(ResearchSearch, '播客RSS说明语义+关键词检索。required_terms/excluded_terms在完整已存说明上做字面包含/排除筛选，筛选候选时使用这些参数，不从相关片段推断全文不含。返回候选总数、展示数和逐项全文检查；索引覆盖不是阅读全文或全目录否定证明。只代表RSS文字，不能保证音频无某内容。', 'server', 'podcast', 'episode', changes_state=False, capability='research_v3', retry='explicit_read', retention='document'),
    'read_episode_description': ToolSpec(EpisodeRead, '按已查询的真实节目/分集ID读取RSS说明，offset分页，每次最多4000字符。has_more=true时未阅读全文；不是音频转写。', 'server', 'podcast', 'episode', changes_state=False, capability='research_v3', retry='explicit_read', retention='document'),
    'search_catalog': ToolSpec(Search, '搜索本机真实播客目录，支持中文、分类、全拼和拼音首字母。返回最多8个候选，total是总命中，returned/omitted标明实际展示和省略数量；has_more为true时需缩小关键词，不把前8项当全部。多个匹配时请用户选择。', 'device', 'podcast', 'podcast', changes_state=False, capability='', retry='explicit_read', retention='receipt'),
    'list_episodes': ToolSpec(Episodes, '查询真实RSS分集，每页最多8项，offset分页。query仅匹配标题文字；episode_number精确匹配标题开头期号；position选择指定order完整列表的第N条(从1起)，与标题期号不同。position/episode_number/query不能混用，position时offset必须0。order=newest按时间倒序，source按节目页原始顺序，reverse_source按节目页倒序。feed_total是总条目数，不是最大期号；零匹配仅说明本次条件无匹配。仅latest_verified=true能确定最新一期。', 'device', 'podcast', 'episode', changes_state=False, capability='', retry='explicit_read', retention='receipt'),
    'get_podcast_details': ToolSpec(PodcastDetails, '读取已查询节目ID的真实RSS节目简介、作者与目录统计（新版客户端提供catalog_summary）。统计覆盖完整可用列表的标题编号、缺口、重复、未识别数字期号项与解析筛除计数，用于核查总集数和期号差异；返回来源及刷新/缓存状态。', 'device', 'podcast', 'podcast', changes_state=False, capability='', retry='explicit_read', retention='receipt'),
    'search_podcast_content': ToolSpec(ContentSearch, '按内容查找分集。keywords填1到4个简短关键词（每词2到30字），将每条分集的标题与简介合并后匹配全部关键词，不要求标题和简介分别都含这些词；不要填整句。留空podcast_id搜索全部内置节目，指定时必须是已知ID。最多返回4个相关分集及RSS原文依据；覆盖不全/缓存会明确标记。不是语义搜索，也不代表已听过音频。', 'device', 'podcast', 'episode', changes_state=False, capability='', retry='explicit_read', retention='receipt'),
    'get_playback_state': ToolSpec(Empty, '查询手机当前选中的分集及所属节目ID/名称、实际播放或暂停状态、倍速与睡眠定时。不论播放还是暂停都可查询；没有选中或身份尚未同步时身份字段为空。', 'device', 'device', 'playback', changes_state=False, capability='', retry='explicit_read', retention='receipt'),
    'play_episode': ToolSpec(Play, '播放此前查询结果中的真实 episode_id，恢复既有进度，并保留该节目的完整循环队列。', 'device', 'device', 'playback', changes_state=True, capability='', retry='never_replay', retention='receipt'),
    'pause_playback': ToolSpec(Empty, '暂停手机当前播放。', 'device', 'device', 'playback', changes_state=True, capability='', retry='never_replay', retention='receipt'),
    'resume_playback': ToolSpec(Empty, '继续手机当前播放；没有选中分集时不能执行。', 'device', 'device', 'playback', changes_state=True, capability='', retry='never_replay', retention='receipt'),
    'previous_episode': ToolSpec(Empty, '切换到手机当前完整队列的上一集并播放，首集向前会循环到末集；没有队列时不执行。', 'device', 'device', 'playback', changes_state=True, capability='', retry='never_replay', retention='receipt'),
    'next_episode': ToolSpec(Empty, '切换到手机当前完整队列的下一集并播放，末集向后会循环到首集；没有队列时不执行。', 'device', 'device', 'playback', changes_state=True, capability='', retry='never_replay', retention='receipt'),
    'set_playback_speed': ToolSpec(Speed, '设置手机播放倍速，可选0.75、1、1.25、1.5、1.75、2倍；不改变播放或暂停状态。', 'device', 'device', 'playback', changes_state=True, capability='', retry='never_replay', retention='receipt'),
    'set_sleep_timer': ToolSpec(Timer, '设置手机睡眠定时，minutes 为 1..180；0 取消，-1 本集结束时暂停。', 'device', 'device', 'playback', changes_state=True, capability='', retry='never_replay', retention='receipt'),
}

DEFINITIONS = [{'type': 'function', 'function': {'name': name, 'description': spec.description,
                'parameters': spec.input_model.model_json_schema()}} for name, spec in TOOLS.items()]

def definitions(extended_episodes=False, research=False):
    available = [d for d in DEFINITIONS if research or not TOOLS[d['function']['name']].capability]
    if extended_episodes:
        return available
    return [d if d['function']['name'] != 'list_episodes' else {'type': 'function', 'function': {
        'name': 'list_episodes', 'description': '查询已知节目真实RSS分集，query仅按标题关键词匹配，最多8项。此客户端不支持列表序号和分页，不能把总数当标题期号；仅latest_verified=true可确认最新一期。',
        'parameters': LegacyEpisodes.model_json_schema()}} for d in available]




def validated_calls(message, extended_episodes=True):
    calls = message.get('tool_calls') or []
    if len(calls) > 8:
        raise ValueError('At most eight tools per model turn')
    clean = []
    seen = set()
    for call in calls:
        ToolCall.model_validate(call)
        if call['id'] in seen:
            raise ValueError('Duplicate tool call ID')
        seen.add(call['id'])
        name = call['function']['name']
        model = TOOLS[name].input_model
        if name == 'list_episodes' and not extended_episodes:
            model = LegacyEpisodes
        args = model.model_validate(json.loads(call['function']['arguments']))
        clean.append({'id': call['id'], 'name': name, 'arguments': args.model_dump()})
    return clean

SERVER_TOOLS = frozenset(name for name, spec in TOOLS.items() if spec.location == 'server')
READ_ONLY = frozenset(name for name, spec in TOOLS.items() if spec.location == 'device' and not spec.changes_state)

def scoped_tools(scope):
    return {name for name, spec in TOOLS.items() if spec.scope == scope}
