"""Validate derived results without rewriting device receipts or their fingerprints."""
from copy import deepcopy
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from maoer_agent.tools.definitions import TOOLS
from maoer_agent.core.errors import error_message


class Item(BaseModel):
    model_config = ConfigDict(strict=True, extra='allow')
    kind: Literal['podcast', 'episode', 'web']
    id: str = Field(min_length=1, max_length=2000)
    # Search providers may return a valid URL with an empty title.
    title: str = Field(max_length=300)
    podcast_id: str | None = None
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


def checked_result(call, raw):
    """Accept legacy optional fields; a malformed success never authorizes more controls."""
    try:
        parsed = Result.model_validate(raw)
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
                if parsed.has_more is True and call['name'] in ('list_episodes', 'read_web_page', 'read_episode_description', 'query_library', 'list_recent_updates'):
                    if parsed.next_offset is None or parsed.next_offset <= call['arguments'].get('offset', 0):
                        raise ValueError('invalid_page')
        # Don't inject defaults or coerce legacy values. Raw JSON stays immutable.
        return deepcopy(raw)
    except (ValidationError, ValueError, KeyError, TypeError):
        return {'status': 'unknown', 'code': 'tool_result_unverified',
                'note': error_message('tool_result_unverified')}


def outcome_facts(messages):
    """Observed tool outcomes, not a claim to have inferred all user requirements."""
    calls, observations = {}, []
    for message in messages:
        for call in message.get('tool_calls') or []:
            calls[call['id']] = call['function']['name']
        if message['role'] == 'tool' and message['tool_call_id'] in calls:
            import json
            result = json.loads(message['content'])
            row = {'call_id': message['tool_call_id'], 'tool': calls[message['tool_call_id']],
                   'status': result.get('status', 'unknown')}
            for key in ('executed', 'code', 'reason', 'playing', 'episode_id', 'playback_speed',
                        'sleep_remaining_ms', 'sleep_end_of_episode', 'has_more'):
                if key in result: row[key] = result[key]
            if isinstance(result.get('items'), list):
                row['evidence'] = [{'id': i.get('id'), 'citation': i.get('citation'),
                                    'has_excerpt': bool(i.get('evidence')), 'kind': i.get('kind')}
                                   for i in result['items']]
            observations.append(row)
    return {'scope': 'observed_calls_only_not_user_goal_verdict', 'observations': observations}
