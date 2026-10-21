"""Validate derived results without rewriting device receipts or their fingerprints."""
from copy import deepcopy
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from maoer_agent.tools.definitions import TOOLS
from maoer_agent.core.errors import error_message
from maoer_agent.contracts.research import RSS_SEARCH_PAGE_SIZE

RSS_STATISTIC_FIELDS = {
    'total_passages': '本次检索资料中的片段总数，重复文本窗口分别计数。',
    'semantic_indexed_passages': '已有向量的片段数，重复文本窗口分别计数；与total_passages比较覆盖数。',
    'semantic_unique_vectors': '已有向量的唯一键数量，相同标题及片段文本复用同一个向量键。',
    'semantic_query_available': '本次查询向量是否可用，与已存片段向量覆盖数分别报告。',
    'matched_episodes': '达到检索评分阈值的分集候选数。',
    'returned': '本页返回的候选数，has_more和next_offset用于续页。',
}


class Item(BaseModel):
    model_config = ConfigDict(strict=True, extra='allow')
    kind: Literal['podcast', 'episode', 'web']
    id: str = Field(min_length=1, max_length=2000)
    # Search providers may return a valid URL with an empty title.
    title: str = Field(max_length=300)
    podcast_id: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    source_url: str | None = None
    evidence: str | None = None
    document_id: str | None = None
    offset: int | None = Field(default=None, ge=0)
    fetched_at: int | None = Field(default=None, ge=0, le=253402214400)
    citation: int | None = Field(default=None, ge=1, le=48)


class Result(BaseModel):
    model_config = ConfigDict(strict=True, extra='allow')
    status: Literal['ok', 'accepted', 'unknown', 'error']
    items: list[Item] | None = Field(default=None, max_length=8)
    playing: bool | None = None
    buffering: bool | None = None
    episode_id: str | None = None
    playback_speed: float | None = None
    sleep_remaining_ms: int | None = Field(default=None, ge=0)
    sleep_end_of_episode: bool | None = None
    has_more: bool | None = None
    next_offset: int | None = Field(default=None, ge=-1)


class RssSearchResult(Result):
    items: list[Item] | None = Field(default=None, max_length=RSS_SEARCH_PAGE_SIZE)


def checked_result(call, raw):
    """Accept legacy optional fields; a malformed success never authorizes more controls."""
    try:
        result_type = RssSearchResult if call['name'] == 'search_rss' else Result
        parsed = result_type.model_validate(raw)
        spec = TOOLS[call['name']]
        # Android list_episodes has always used -1 for the final page. This
        # sentinel is not a valid offset for a continuation or a text reader.
        if parsed.next_offset == -1 and not (call['name'] == 'list_episodes' and parsed.has_more is False):
            raise ValueError('invalid_end_offset')
        if parsed.status == 'ok':
            if spec.result_kind == 'playback':
                if parsed.playing is None:
                    raise ValueError('missing_playing')
                args = call['arguments']
                if call['name'] in ('play_episode', 'resume_playback', 'previous_episode', 'next_episode'):
                    if not parsed.playing or not parsed.episode_id or parsed.buffering is True:
                        raise ValueError('unobserved_playback')
                    if call['name'] == 'play_episode' and parsed.episode_id != args['episode_id']:
                        raise ValueError('wrong_episode')
                if call['name'] == 'pause_playback' and parsed.playing:
                    raise ValueError('unobserved_pause')
                if call['name'] == 'set_playback_speed' and parsed.playback_speed != args['speed']:
                    raise ValueError('unobserved_speed')
                if call['name'] == 'set_sleep_timer':
                    minutes = args['minutes']
                    end, remaining = parsed.sleep_end_of_episode, parsed.sleep_remaining_ms
                    if end is None or remaining is None or not (
                        (minutes == -1 and end) or (minutes == 0 and not end and remaining == 0)
                        or (minutes > 0 and not end and minutes * 60000 - 5000 <= remaining <= minutes * 60000)):
                        raise ValueError('unobserved_timer')
            else:
                if parsed.items is None:
                    raise ValueError('missing_items')
                for item in parsed.items:
                    if item.kind not in ({'podcast', 'episode'} if spec.result_kind == 'library' else {spec.result_kind}):
                        raise ValueError('wrong_kind')
                    if item.kind == 'podcast' and len(item.id) > 150:
                        raise ValueError('podcast_id')
                    if item.kind == 'episode':
                        if not item.podcast_id or len(item.podcast_id) > 150:
                            raise ValueError('missing_podcast')
                        if call['arguments'].get('podcast_id') and item.podcast_id != call['arguments']['podcast_id']:
                            raise ValueError('wrong_podcast')
                    if call['name'] == 'read_episode_description' and item.id != call['arguments']['episode_id']:
                        raise ValueError('wrong_episode')
                if parsed.has_more is True and call['name'] in ('search_rss', 'list_episodes', 'read_web_page', 'read_episode_description', 'query_library', 'list_recent_updates'):
                    if parsed.next_offset is None or parsed.next_offset <= call['arguments'].get('offset', 0):
                        raise ValueError('invalid_page')
        # Don't inject defaults or coerce legacy values. Raw JSON stays immutable.
        return deepcopy(raw)
    except (ValidationError, ValueError, KeyError, TypeError):
        return {'status': 'unknown', 'code': 'tool_result_unverified',
                'note': error_message('tool_result_unverified')}


def outcome_facts(messages):
    """Observed tool outcomes, not a claim to have inferred all user requirements."""
    calls, observations, library_pages = {}, [], {}
    for message in messages:
        for call in message.get('tool_calls') or []:
            calls[call['id']] = call['function']['name']
        if message['role'] == 'tool' and message['tool_call_id'] in calls:
            import json
            result = json.loads(message['content'])
            row = {'call_id': message['tool_call_id'], 'tool': calls[message['tool_call_id']],
                   'status': result.get('status', 'unknown')}
            for key in ('executed', 'code', 'reason', 'playing', 'episode_id', 'playback_speed',
                        'sleep_remaining_ms', 'sleep_end_of_episode', 'has_more', 'observation_scope'):
                if key in result: row[key] = result[key]
            if isinstance(result.get('items'), list):
                row['evidence'] = [{'id': i.get('id'), 'citation': i.get('citation'),
                                    'has_excerpt': bool(i.get('evidence')), 'kind': i.get('kind')}
                                   for i in result['items']]
                if row['tool'] == 'query_library' and result.get('status') == 'ok':
                    episodes = [i for i in result['items'] if i.get('kind') == 'episode']
                    row['page_counts'] = {'tab': result.get('tab'), 'offset': result.get('offset', 0),
                        'items': len(result['items']), 'episodes': len(episodes),
                        'podcasts': sum(i.get('kind') == 'podcast' for i in result['items']),
                        'favorite_episodes': sum(i.get('favorite') is True for i in episodes),
                        'favorite_unknown': sum(type(i.get('favorite')) is not bool for i in episodes)}
                    key = (result.get('tab'), result.get('favorite_kind', 'all'), result.get('query', ''))
                    library_pages.setdefault(key, {})[result.get('offset', 0)] = result['items']
                if row['tool'] == 'search_rss' and result.get('status') == 'ok':
                    row['retrieval_counts'] = {'candidates': result.get('matched_episodes'),
                        'returned': len(result['items']), 'offset': result.get('offset', 0)}
            observations.append(row)
    totals = []
    for (tab, favorite_kind, query), pages in library_pages.items():
        unique = {(i['kind'], i['id']): i for _, items in sorted(pages.items()) for i in items}
        episodes = [i for i in unique.values() if i['kind'] == 'episode']
        programs = {}
        for item in episodes:
            name = item.get('podcast_title') or item['podcast_id']
            programs[name] = programs.get(name, 0) + 1
        totals.append({'tab': tab, 'favorite_kind': favorite_kind, 'query': query,
            'observed_items': len(unique), 'observed_episodes': len(episodes),
            'favorite_episodes': sum(i.get('favorite') is True for i in episodes),
            'favorite_unknown': sum(type(i.get('favorite')) is not bool for i in episodes),
            'episodes_by_podcast': programs})
    return {'scope': 'observed_calls_only_not_user_goal_verdict', 'observations': observations,
            **({'library_totals': totals} if totals else {})}
