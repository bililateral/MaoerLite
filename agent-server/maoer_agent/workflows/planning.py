"""Pure tool-scope and identity/selection planning; no execution or task persistence."""
import json
from maoer_agent.tools.definitions import definitions
from maoer_agent.workflows.intent import allowed_tools
from maoer_agent.workflows.selection import selection_identity_refresh, playback_preflight, episode_selection_question
from maoer_agent.memory.history import references, last_turn
from maoer_agent.contracts.errors import ModelFailure


def tool_scope(route, capabilities, extended, research, final_only):
    available = definitions(extended, research, capabilities)
    allowed = allowed_tools(route) if research else {d['function']['name'] for d in available}
    if any(d['function']['name'] == 'query_library' for d in available):
        allowed.add('query_library')
    return ([] if final_only else [d for d in available if d['function']['name'] in allowed]), allowed


def prepare_calls(messages, memory, calls, message, allowed, run_id, rounds):
    invalid_identities = {}
    if calls:
        # No tool is dispatched until complete model output and validation.
        known = list(references(memory))
        for previous in last_turn(messages):
            if previous['role'] == 'tool':
                result = json.loads(previous['content'])
                if result.get('status') == 'ok':
                    known.extend(result.get('items', []))
        refresh_identity = selection_identity_refresh(messages, calls, known, run_id)
        if refresh_identity:
            calls = [refresh_identity]
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': refresh_identity['id'], 'type': 'function',
                'function': {'name': 'get_playback_state', 'arguments': '{}'}}]}
        for call in calls:
            args = call['arguments']
            if call['name'] in ('list_episodes', 'get_podcast_details') and not any(
                    (item.get('id') == args['podcast_id'] and item.get('kind') == 'podcast') or
                    (item.get('podcast_id') == args['podcast_id'] and item.get('kind') == 'episode') for item in known):
                invalid_identities[call['id']] = 'unverified_podcast_id'
            if call['name'] in ('play_episode', 'read_episode_description') and not any(
                    item.get('id') == args['episode_id'] and item.get('podcast_id') == args['podcast_id']
                    and item.get('kind') == 'episode' for item in known):
                invalid_identities[call['id']] = 'unverified_episode_id'
        ids = [c['id'] for c in calls]
        seen = {c['id'] for m in messages for c in m.get('tool_calls', [])}
        if any(not i or i in seen for i in ids):
            raise ModelFailure('invalid_tool_call')
        executable = [c for c in calls if c['id'] not in invalid_identities and
                      c['name'] in allowed and not episode_selection_question(messages, [c])]
        refresh = playback_preflight(messages, executable, run_id, rounds)
        if refresh:
            if any(call['id'] in seen for call in refresh):
                raise ModelFailure('invalid_tool_call')
            calls = refresh
            message = {'role': 'assistant', 'content': '播放前先核对本轮RSS，再继续完成用户要求；原控制计划尚未执行。用户选择旧列表位置时仍按原展示列表，不用补查列表重新编号；用户要求最新时按刷新结果选择。',
                       'tool_calls': [{'id': c['id'], 'type': 'function', 'function': {
                           'name': c['name'], 'arguments': json.dumps(c['arguments'], ensure_ascii=False)}} for c in calls]}
    return message, calls, invalid_identities
