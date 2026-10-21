"""Completed query facts for direct presentation, without interpreting app state."""
from maoer_agent.memory.history import last_turn
from maoer_agent.workflows.episode_pages import observations


def reply_step(route, messages, names):
    if (route.get('control_actions')
            or route.get('page_lookup', {}).get('requested')
            or route.get('episode_criteria', {}).get('requested')):
        return None
    if route.get('library'):
        return None
    request = route.get('unsupported_request', {})
    actions = request.get('actions', [])
    if actions and request.get('exclusive') and not set(actions) & names:
        return {'kind': 'unsupported', 'actions': list(dict.fromkeys(actions))}
    if actions:
        return None
    if not route.get('response_needs', {}).get('brief_episode_intro'):
        return None
    items = {i['id']: i for c, r in observations(last_turn(messages))
             if c['name'] == 'read_episode_description' and r.get('status') == 'ok'
             for i in r.get('items', [])}
    if len(items) != 1:
        return None
    item = next(iter(items.values()))
    if item.get('evidence') and item.get('evidence_truncated') is False and len(item['evidence']) <= 1000:
        return {'kind': 'introduction', 'item': item}
    return None


def library_reply_step(route, messages):
    if not route.get('response_needs', {}).get('library_listing') or route.get('control_actions'):
        return None
    results = [r for c, r in observations(last_turn(messages)) if c['name'] == 'query_library']
    if not results or any(r.get('status') != 'ok' for r in results):
        return None
    if len({(r['tab'], r.get('favorite_kind', 'all'), r.get('query', '')) for r in results}) != 1:
        return None
    pages = {r['offset']: r for r in results}
    pages = dict(sorted(pages.items()))
    if route['response_needs'].get('last_library_page'):
        last = next((r for r in reversed(list(pages.values()))
            if not r.get('has_more') and r['offset'] + len(r['items']) == r['total']), None)
        if last is None:
            return None
        pages = {last['offset']: last}
    return {'kind': 'library', 'pages': list(pages.values())}
