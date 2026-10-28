"""Exhaustive literal title delivery from actual RSS page receipts."""
from maoer_agent.memory.history import last_turn
from maoer_agent.workflows.goals import observations
from maoer_agent.tools.definitions import TOOLS


def request(route):
    criteria = route.get('episode_criteria', {})
    goals = route.get('task_plan', {}).get('goals', [])
    if (criteria.get('requested') and criteria.get('match_mode') == 'literal'
            and criteria.get('delivery') == 'all_titles' and criteria.get('podcast_title')
            and (criteria.get('required_terms') or criteria.get('excluded_terms'))
            and len(goals) == 1 and goals[0]['kind'] == 'catalog_query' and not goals[0].get('page_request')
            and not route.get('control_actions') and not any(route.get(k) for k in ('web','device','library'))):
        return criteria, goals[0]
    return None


def pages(route, messages):
    selected = request(route)
    if not selected:
        return None
    criteria, goal = selected
    podcasts = {item['id'] for _, result in observations(messages) if result.get('status') == 'ok'
                for item in result.get('items', []) if item.get('kind') == 'podcast'
                and item.get('title', '').strip() == criteria['podcast_title'].strip()}
    if len(podcasts) != 1:
        return None
    args = {'mode': 'literal', 'podcast_id': next(iter(podcasts)),
            'query': (criteria.get('topic') or goal.get('query') or criteria['podcast_title'])[:300],
            'required_terms': criteria.get('required_terms', []), 'excluded_terms': criteria.get('excluded_terms', []),
            'max_duration_seconds': criteria.get('max_duration_seconds', 0), 'offset': 0}
    actual = observations(last_turn(messages));chain = [];seen = set()
    while args['offset'] not in seen:
        seen.add(args['offset'])
        normalize=TOOLS['search_rss'].input_model.model_validate
        found = next(((c, r) for c, r in reversed(actual) if c['name'] == 'search_rss'
                      and normalize(c['arguments']) == normalize(args)), None)
        if found is None:
            return criteria, goal, chain, args
        chain.append(found);result = found[1]
        if result.get('status') != 'ok' or result.get('has_more') is not True:
            return criteria, goal, chain, None
        cursor = result.get('next_offset')
        if type(cursor) is not int or cursor <= args['offset']:
            return None
        args = {**args, 'offset': cursor}
    return None


def next_call(route, messages, names):
    if not request(route):
        return None
    view = pages(route, messages)
    if view is None:
        title = request(route)[0]['podcast_title']
        if 'search_catalog' in names and not any(c['name'] == 'search_catalog' for c, _ in observations(last_turn(messages))):
            return {'name': 'search_catalog', 'arguments': {'query': title}}
    elif view[3] is not None and 'search_rss' in names:
        return {'name': 'search_rss', 'arguments': view[3]}
    return None


def completion(route, messages):
    view = pages(route, messages)
    if not view or view[3] is not None or not view[2]:
        return None
    criteria, goal, chain, _ = view
    items = [];seen = set();cursor = 0
    total = chain[0][1].get('matched_episodes')
    snapshot = chain[0][1].get('snapshot')
    for call, result in chain:
        literal = result.get('literal_filter', {})
        if (result.get('status') != 'ok' or result.get('mode') != 'literal' or not result.get('exhaustive')
                or result.get('retrieval_scope') != 'exhaustive_stored_description_literal_findings'
                or result.get('match_count_kind') != 'literal_findings' or result.get('offset') != cursor
                or result.get('matched_episodes') != total
                or not snapshot or result.get('snapshot') != snapshot
                or literal.get('required_terms') != criteria.get('required_terms', [])
                or literal.get('excluded_terms') != criteria.get('excluded_terms', [])):
            return None
        page = result.get('items', [])
        if result.get('returned') != len(page) or result.get('next_offset') != cursor + len(page):
            return None
        for item in page:
            predicate = item.get('description_checks', {})
            if (item.get('kind') != 'episode' or item.get('podcast_id') != call['arguments']['podcast_id']
                    or item['id'] in seen or not item.get('citation') or not predicate.get('passes')
                    or not predicate.get('description_complete')
                    or predicate.get('required_terms') != criteria.get('required_terms', [])
                    or predicate.get('excluded_terms') != criteria.get('excluded_terms', [])):
                return None
            seen.add(item['id']);items.append(item)
        cursor = result['next_offset']
    if cursor != total or chain[-1][1].get('has_more') is not False:
        return None
    return {'kind': 'literal_titles', 'total':total, 'items': items, 'goal_id': goal['id'],
            'proof_calls': [c['id'] for c, _ in chain]}
