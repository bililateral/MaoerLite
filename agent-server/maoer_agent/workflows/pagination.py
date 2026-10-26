"""Catalog page targets and completion from delivered history and paired receipts."""
from maoer_agent.context.route import displayed_episode_pages
from maoer_agent.memory.history import last_turn
from maoer_agent.tools.results import receipt_evidence, catalog_page_chain, catalog_page_view


def page_target(goal,messages):
    request=goal.get('page_request')
    if goal.get('kind')!='catalog_query' or not request:return None
    if request['operation']!='next':
        return {'offset':request.get('offset',0) if request['operation']=='offset' else 0}
    pages=displayed_episode_pages(messages)
    reference=request.get('reference','');title=request.get('podcast_title','')
    pages=[p for p in pages if (not reference or p['reference']==reference)
           and (not title or p.get('podcast_title')==title)]
    if not pages:return None
    query=pages[-1].get('query')
    if not query or not query.get('has_more') or 'next_offset' not in query:return None
    return {**query['arguments'],'offset':query['next_offset']}


def page_observations(goal,messages):
    target=page_target(goal,messages)
    if target is None:return []
    rows=receipt_evidence(messages)
    title=goal['page_request'].get('podcast_title','')
    podcasts={i['id']:i.get('title','') for r in rows if r['result'].get('status')=='ok'
              for i in r['result'].get('items',[]) if i.get('kind')=='podcast'}
    current=[r for r in receipt_evidence(last_turn(messages))
             if not title or podcasts.get(r['arguments'].get('podcast_id'))==title]
    return catalog_page_chain(current,target)


def observed_page(goal,messages):
    chain=page_observations(goal,messages)
    count=(goal.get('page_request') or {}).get('count',0)
    view=catalog_page_view(chain,count)
    if view and (not count or len(view['result']['items'])>=count
                 or chain[-1]['result'].get('has_more') is False):return view
    return None


def page_continuation(goal,messages):
    chain=page_observations(goal,messages)
    if not chain or observed_page(goal,messages):return None
    last=chain[-1];result=last['result'];offset=result.get('next_offset')
    if not result.get('has_more') or not result.get('items') or offset is None or offset<=last['arguments'].get('offset',0):return None
    args={**last['arguments'],'offset':offset}
    # An existing failed receipt is not authorization to retry it automatically.
    if any(r['tool']=='list_episodes' and r['arguments']==args for r in receipt_evidence(last_turn(messages))):return None
    return {'name':'list_episodes','arguments':args}


def page_delivery(route,messages):
    """A plain page has a factual renderer; other goals keep semantic delivery."""
    goals=route.get('task_plan',{}).get('goals',[])
    if len(goals)!=1 or not goals[0].get('page_request'):return None
    if (route.get('control_actions') or any(route.get(k) for k in ('web','device','library'))
            or route.get('episode_criteria',{}).get('requested')
            or route.get('page_lookup',{}).get('requested')):return None
    needs=route.get('response_needs',{})
    if any(needs.get(k) for k in ('app_guidance','retrieval_statistics','brief_episode_intro')):return None
    row=observed_page(goals[0],messages)
    if row:return {'kind':'episode_page','result':row['result'],'titles_only':needs.get('titles_only',False)}
    return None
