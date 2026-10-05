"""Only these typed tools may be dispatched to the Android application."""
import json
from typing import Literal
from pydantic import Field, field_validator, model_validator
from protocol import StrictModel, ToolCall
from agent_prompts import SYSTEM


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


TOOLS = {
    'search_catalog': (Search, '搜索本机真实播客目录，支持中文、分类、全拼和拼音首字母。返回最多8个候选，total是总命中，returned/omitted标明实际展示和省略数量；has_more为true时需缩小关键词，不把前8项当全部。多个匹配时请用户选择。'),
    'list_episodes': (Episodes, '查询真实RSS分集，每页最多8项，offset分页。query仅匹配标题文字；episode_number精确匹配标题开头期号；position选择指定order完整列表的第N条(从1起)，与标题期号不同。position/episode_number/query不能混用，position时offset必须0。order=newest按时间倒序，source按节目页原始顺序，reverse_source按节目页倒序。feed_total是总条目数，不是最大期号；零匹配仅说明本次条件无匹配。仅latest_verified=true能确定最新一期。'),
    'get_podcast_details': (PodcastDetails, '读取已查询节目ID的真实RSS节目简介、作者与目录统计（新版客户端提供catalog_summary）。统计覆盖完整可用列表的标题编号、缺口、重复、未识别数字期号项与解析筛除计数，用于核查总集数和期号差异；返回来源及刷新/缓存状态。'),
    'search_podcast_content': (ContentSearch, '按内容查找分集。keywords填1到4个简短关键词（每词2到30字），将每条分集的标题与简介合并后匹配全部关键词，不要求标题和简介分别都含这些词；不要填整句。留空podcast_id搜索全部内置节目，指定时必须是已知ID。最多返回4个相关分集及RSS原文依据；覆盖不全/缓存会明确标记。不是语义搜索，也不代表已听过音频。'),
    'get_playback_state': (Empty, '查询手机当前选中的分集及所属节目ID/名称、实际播放或暂停状态、倍速与睡眠定时。不论播放还是暂停都可查询；没有选中或身份尚未同步时身份字段为空。'),
    'play_episode': (Play, '播放此前查询结果中的真实 episode_id，恢复既有进度，并保留该节目的完整循环队列。'),
    'pause_playback': (Empty, '暂停手机当前播放。'),
    'resume_playback': (Empty, '继续手机当前播放；没有选中分集时不能执行。'),
    'previous_episode': (Empty, '切换到手机当前完整队列的上一集并播放，首集向前会循环到末集；没有队列时不执行。'),
    'next_episode': (Empty, '切换到手机当前完整队列的下一集并播放，末集向后会循环到首集；没有队列时不执行。'),
    'set_playback_speed': (Speed, '设置手机播放倍速，可选0.75、1、1.25、1.5、1.75、2倍；不改变播放或暂停状态。'),
    'set_sleep_timer': (Timer, '设置手机睡眠定时，minutes 为 1..180；0 取消，-1 本集结束时暂停。'),
}

DEFINITIONS = [{'type': 'function', 'function': {'name': name, 'description': description,
                'parameters': model.model_json_schema()}} for name, (model, description) in TOOLS.items()]

def definitions(extended_episodes=False):
    if extended_episodes:
        return DEFINITIONS
    return [d if d['function']['name'] != 'list_episodes' else {'type': 'function', 'function': {
        'name': 'list_episodes', 'description': '查询已知节目真实RSS分集，query仅按标题关键词匹配，最多8项。此客户端不支持列表序号和分页，不能把总数当标题期号；仅latest_verified=true可确认最新一期。',
        'parameters': LegacyEpisodes.model_json_schema()}} for d in DEFINITIONS]




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
        model, _ = TOOLS[name]
        if name == 'list_episodes' and not extended_episodes:
            model = LegacyEpisodes
        args = model.model_validate(json.loads(call['function']['arguments']))
        clean.append({'id': call['id'], 'name': name, 'arguments': args.model_dump()})
    return clean
