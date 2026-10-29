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
            for call in calls:
                if call['arguments'].get('podcast_id'):
                    invalid_identities[call['id']] = 'playback_identity_refresh_required'
        for call in calls:
            args = call['arguments']
            if call['name'] in ('list_episodes', 'get_podcast_details') and not any(
                    (item.get('id') == args['podcast_id'] and item.get('kind') == 'podcast') or
                    (item.get('podcast_id') == args['podcast_id'] and item.get('kind') == 'episode') for item in known):
                invalid_identities.setdefault(call['id'], 'unverified_podcast_id')
            if call['name'] in ('play_episode', 'read_episode_description') and not any(
                    item.get('id') == args['episode_id'] and item.get('podcast_id') == args['podcast_id']
                    and item.get('kind') == 'episode' for item in known):
                invalid_identities.setdefault(call['id'], 'unverified_episode_id')
        ids = [c['id'] for c in calls]
        seen = {c['id'] for m in messages for c in (m.get('tool_calls') or [])}
        if any(not i or i in seen for i in ids):
            raise ModelFailure('invalid_tool_call')
        executable = [c for c in calls if c['id'] not in invalid_identities and
                      c['name'] in allowed and not episode_selection_question(messages, [c])]
        refresh = playback_preflight(messages, executable, run_id, rounds)
        if refresh:
            pending={c['arguments']['podcast_id'] for c in refresh}
            for call in executable:
                if call['name']=='play_episode' and call['arguments']['podcast_id'] in pending:
                    invalid_identities[call['id']]='rss_refresh_required'
    return message, calls, invalid_identities
