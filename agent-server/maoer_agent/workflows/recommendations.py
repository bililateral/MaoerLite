"""Resolve an added episode filter into a concrete, evidence-backed query."""
from maoer_agent.workflows.episode_pages import observations
from maoer_agent.memory.history import last_turn
from maoer_agent.workflows.goals import observations as goal_observations
from maoer_agent.tools.definitions import TOOLS


def duration_candidates(route,messages):
    """A requested filtered list uses matched RSS identities and numeric metadata."""
    criteria=route.get('episode_criteria',{})
    plan=route.get('task_plan',{}).get('goals',[])
    if (not criteria.get('requested') or criteria.get('delivery')!='candidates'
            or not criteria.get('max_duration_seconds') or criteria.get('preference')
            or len(plan)!=1 or plan[0]['kind']!='catalog_query' or plan[0].get('page_request')
            or any(route.get(k) for k in ('web','device','library')) or route.get('control_actions')):
        return None
    podcasts={i['id'] for _,r in observations(messages) if r.get('status')=='ok'
              for i in r.get('items',[]) if i.get('kind')=='podcast' and i.get('title')==criteria['podcast_title']}
    if len(podcasts)!=1:return None
    podcast=next(iter(podcasts));matches=[]
    for call,result in goal_observations(last_turn(messages)):
        args=call['arguments']
        if (call['name']=='search_rss' and result.get('status')=='ok' and result.get('relevance_available')
                and args.get('podcast_id')==podcast and args.get('query')==criteria['topic']
                and args.get('max_duration_seconds')==criteria['max_duration_seconds']):
            matches.append((call,result))
    if not matches or matches[-1][1].get('has_more'):return None
    items={}
    for _,result in matches:
        for item in result.get('items',[]):
            duration=item.get('duration_seconds',0)
            if (item.get('kind')!='episode' or item.get('podcast_id')!=podcast or not item.get('citation')
                    or not item.get('evidence') or not 0<duration<=criteria['max_duration_seconds']):return None
            items[item['id']]=item
    if not items:return None
    return {'kind':'duration_candidates','items':list(items.values()),'goal_id':plan[0]['id'],
            'proof_calls':[c['id'] for c,_ in matches],'max_duration_seconds':criteria['max_duration_seconds']}


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
    normalize=TOOLS['search_rss'].input_model.model_validate
    if any(c['name'] == 'search_rss' and normalize(c['arguments']) == normalize(args) for c, _ in current):
        return None
    return {'name': 'search_rss', 'arguments': args}
