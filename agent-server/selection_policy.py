"""Selection evidence and confirmation policy; returns facts, not final replies."""
import hashlib
import json
from agent_model import ModelFailure
from agent_memory import last_turn, PLAYBACK_SNAPSHOT_TOOLS


def selection_identity_refresh(messages, calls, known, run_id):
    """Upgrade a legacy state receipt before using its missing podcast identity.

    No ID is inferred from an episode string. Re-read once, then let the model
    plan again from the real device result; never repeat an already executed control.
    """
    missing = any(c['arguments'].get('podcast_id') and not any(
        (i.get('kind') == 'podcast' and i.get('id') == c['arguments']['podcast_id']) or
        (i.get('kind') == 'episode' and i.get('podcast_id') == c['arguments']['podcast_id'])
        for i in known) for c in calls)
    if not missing:
        return None
    names, latest = {}, None
    call_id = 'selection_identity_' + run_id
    for m in last_turn(messages):
        for c in m.get('tool_calls') or []:
            names[c['id']] = c['function']['name']
        if m['role'] == 'tool' and names.get(m['tool_call_id']) in PLAYBACK_SNAPSHOT_TOOLS:
            latest = json.loads(m['content'])
    if (call_id in names or not latest or latest.get('status') != 'ok'
            or not latest.get('episode_id') or 'podcast_id' in latest):
        return None
    return {'id': call_id, 'name': 'get_playback_state', 'arguments': {}}



def playback_preflight(messages, calls, run_id, round_number):
    """Refresh cross-turn playback selections before any control in that model batch.

    This is a read dependency, not a natural-language classifier. Discard the stale
    plan and let the model choose again from actual device data and the user request.
    """
    requests, checked, attempted = {}, set(), set()
    for message in last_turn(messages):
        for call in message.get('tool_calls', []):
            if call['function']['name'] == 'list_episodes':
                requests[call['id']] = json.loads(call['function']['arguments'])['podcast_id']
        if message['role'] == 'tool' and message['tool_call_id'] in requests:
            podcast = requests[message['tool_call_id']]
            attempted.add(podcast)
            if json.loads(message['content']).get('status') == 'ok':
                checked.add(podcast)
    pending = list(dict.fromkeys(c['arguments']['podcast_id'] for c in calls
                                if c['name'] == 'play_episode' and c['arguments']['podcast_id'] not in checked))
    if any(podcast in attempted for podcast in pending):
        raise ModelFailure('episode_refresh_failed')
    return [{'id': 'refresh_' + hashlib.sha256(f'{run_id}:{round_number}:{podcast}'.encode()).hexdigest()[:24],
             'name': 'list_episodes', 'arguments': {'podcast_id': podcast, 'query': ''}} for podcast in pending]



def episode_selection_question(messages, calls):
    """Show a position selection once; never silently switch its numeric meaning."""
    starts = [i for i, message in enumerate(messages) if message['role'] == 'user']
    previous = last_turn(messages[:starts[-1]]) if starts else []
    prior_queries, shown = {}, set()
    prior_play_ids = {c['id'] for m in previous for c in m.get('tool_calls', [])
                      if c['function']['name'] == 'play_episode'}
    prior_play = any(m['role'] == 'tool' and m.get('tool_call_id') in prior_play_ids
                     and json.loads(m['content']).get('playing') is True for m in previous)
    if not prior_play:
        for message in previous:
            for call in message.get('tool_calls', []):
                if call['function']['name'] == 'list_episodes':
                    prior_queries[call['id']] = json.loads(call['function']['arguments'])
            if message['role'] == 'tool' and message['tool_call_id'] in prior_queries:
                args = prior_queries[message['tool_call_id']]
                result = json.loads(message['content'])
                if args.get('position') and result.get('status') == 'ok':
                    shown.update((args['podcast_id'], args['position'], args.get('order', 'newest'), item.get('id'))
                                 for item in result.get('items', []))
    queries, missing_numbers, missing_positions, alternatives, numbered, positioned = {}, set(), {}, {}, {}, {}
    for message in last_turn(messages):
        for call in message.get('tool_calls', []):
            if call['function']['name'] == 'list_episodes':
                queries[call['id']] = json.loads(call['function']['arguments'])
        if message['role'] != 'tool' or message['tool_call_id'] not in queries:
            continue
        args = queries[message['tool_call_id']]
        result = json.loads(message['content'])
        if result.get('status') != 'ok':
            continue
        podcast = args['podcast_id']
        number, position = args.get('episode_number', 0), args.get('position', 0)
        if number and not result.get('items'):
            missing_numbers.add((podcast, number))
        if position and not result.get('items'):
            missing_positions[(podcast, position)] = args.get('order', 'newest')
        if number:
            for item in result.get('items', []):
                numbered[(podcast, item.get('id'))] = (number, item.get('title', ''))
        if position:
            for item in result.get('items', []):
                positioned[(podcast, item.get('id'))] = (position, args.get('order', 'newest'), item.get('title', ''))
                if item.get('title_episode_number') != position:
                    alternatives[(podcast, item.get('id'))] = (position, args.get('order', 'newest'), item.get('title', ''))
    for call in calls:
        if call['name'] != 'play_episode':
            continue
        args = call['arguments']
        numbered_item = numbered.get((args['podcast_id'], args['episode_id']))
        if numbered_item and (args['podcast_id'], numbered_item[0]) in missing_positions:
            number, title = numbered_item
            order = missing_positions[(args['podcast_id'], number)]
            return {'reason': 'position_missing_title_number_found', 'number': number, 'order': order, 'title': title}
        alternative = alternatives.get((args['podcast_id'], args['episode_id']))
        if alternative and (args['podcast_id'], alternative[0]) in missing_numbers:
            position, order, title = alternative
            return {'reason': 'title_number_missing_position_found', 'position': position, 'order': order, 'title': title}
        selected = positioned.get((args['podcast_id'], args['episode_id']))
        if selected:
            position, order, title = selected
            if (args['podcast_id'], position, order, args['episode_id']) not in shown:
                return {'reason': 'position_requires_confirmation', 'position': position, 'order': order, 'title': title}
    return None
