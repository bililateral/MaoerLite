"""Resolve an added episode filter into a concrete, evidence-backed query."""
from maoer_agent.workflows.episode_pages import observations
from maoer_agent.memory.history import last_turn


def criteria_call(route, messages, names):
    criteria = route.get('episode_criteria', {})
    if not criteria.get('requested'):
        return None
    if not criteria.get('max_duration_seconds') and not criteria.get('preference'):
        return None
    title = criteria.get('podcast_title', '').strip()
    topic = criteria.get('topic', '').strip()
    if not title or not topic:
        return None
    current = observations(last_turn(messages))
    all_rows = observations(messages)
    podcasts = {item['id'] for _, result in all_rows if result.get('status') == 'ok'
                for item in result.get('items', []) if item.get('kind') == 'podcast'
                and item.get('title', '').strip() == title}
    if not podcasts:
        if 'search_catalog' in names and not any(c['name'] == 'search_catalog' for c, _ in current):
            return {'name': 'search_catalog', 'arguments': {'query': title}}
        return None
    if len(podcasts) != 1 or 'search_rss' not in names:
        return None
    args = {'query': ' '.join(filter(None, (topic, criteria.get('preference', '').strip())))[:300],
            'podcast_id': next(iter(podcasts)), 'max_duration_seconds': criteria.get('max_duration_seconds', 0),
            'offset': 0, 'required_terms': [], 'excluded_terms': []}
    if any(c['name'] == 'search_rss' and c['arguments'] == args for c, _ in current):
        return None
    return {'name': 'search_rss', 'arguments': args}
