"""Content introductions require evidence for the selected episode, not a list page."""
from maoer_agent.contracts.intent import episode_description_required
from maoer_agent.memory.history import last_turn
from maoer_agent.tools.results import receipt_evidence


def selected_episode(messages, call_ids):
    for row in reversed(receipt_evidence(last_turn(messages))):
        if row['call_id'] not in call_ids or row['tool'] != 'list_episodes' or row['result'].get('status') != 'ok':
            continue
        args, result = row['arguments'], row['result']
        items = result.get('items', [])
        exact = result.get('total') == 1 or args.get('position') or args.get('episode_number')
        latest = (args.get('order', 'newest') == 'newest' and not args.get('offset')
                  and not args.get('query') and result.get('latest_verified') is True)
        if items and (exact or latest):
            return items[0]
    return None


def description_items(messages, call_ids):
    target = selected_episode(messages, call_ids)
    return [item for row in receipt_evidence(last_turn(messages)) if row['call_id'] in call_ids
            and row['tool'] in ('search_rss','read_episode_description') and row['result'].get('status') == 'ok'
            for item in row['result'].get('items', []) if item.get('kind') == 'episode'
            and item.get('document_id') and item.get('evidence') and item.get('citation')
            and (target is None or item['id'] == target['id'])]


def description_call(route, goal, messages, call_ids, names):
    if not episode_description_required(route, goal) or 'read_episode_description' not in names:
        return None
    target = selected_episode(messages, call_ids)
    if target is None or description_items(messages, call_ids):
        return None
    if any(row['tool'] == 'read_episode_description' and row['arguments'].get('episode_id') == target['id']
           for row in receipt_evidence(last_turn(messages))):
        return None  # A failed read is not permission to replay it.
    return {'name': 'read_episode_description', 'arguments':
            {'podcast_id': target['podcast_id'], 'episode_id': target['id'], 'offset': 0}}
