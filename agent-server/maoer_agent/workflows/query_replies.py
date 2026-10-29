"""Compute business facts from receipts for the agent's next model input."""
from maoer_agent.memory.history import last_turn
from maoer_agent.workflows.episode_pages import observations
from maoer_agent.workflows.goals import goals, parameter_state, observations as goal_observations, ready, TERMINAL
from maoer_agent.tools.definitions import TOOLS
from maoer_agent.workflows.pagination import page_delivery


def control_receipt_step(route,messages):
    """Expose actual execution results without generating a final answer."""
    plan=route.get('task_plan',{}).get('goals',[])
    if (not plan or any(g['kind'] not in {'control','unsupported'} for g in plan) or route.get('web') or route.get('library')
            or route.get('response_needs',{}).get('app_guidance')
            or route.get('response_needs',{}).get('control_explanation','none')!='none'):return None
    prerequisites={'rss_refresh_required','playback_identity_refresh_required',
                   'unverified_podcast_id','unverified_episode_id'}
    receipts=[(c,r) for c,r in observations(last_turn(messages))
              if c['name'] in TOOLS and TOOLS[c['name']].changes_state
              and not (r.get('executed') is False and r.get('code') in prerequisites)]
    if [c['name'] for c,r in receipts]!=route.get('control_actions',[]):return None
    return {'kind':'control_receipts','receipts':[{'name':c['name'],'arguments':c['arguments'],'result':r} for c,r in receipts],
            'unsupported':list(dict.fromkeys(route.get('unsupported_request',{}).get('actions',[])+
                [g['action'] for g in plan if g['kind']=='unsupported' and g.get('action')]))}


def unsupported_setting_step(route,progress):
    plan=goals(route)
    if len(plan)!=1 or route.get('response_needs',{}).get('app_guidance'):return None
    goal=plan[0]
    if (goal['kind']=='control' and goal.get('action') in ('set_playback_speed','set_sleep_timer')
            and progress.get(goal['id'],{}).get('status')=='unavailable' and parameter_state(goal)=='unsupported'):
        return {'kind':'unsupported_setting','name':goal['action'],'arguments':goal['arguments']}
    return None


def selection_clarification_step(route,progress,messages):
    plan=goals(route)
    if (not plan or any(g['kind'] not in {'control','unsupported'} for g in plan)
            or not any(g.get('action')=='play_episode' and progress.get(g['id'],{}).get('status')=='waiting_user' for g in plan)):
        return None
    selection=route.get('page_lookup',{})
    if selection.get('selector_kind')!='displayed_page' or selection.get('page_reference'):return None
    receipts=[{'name':c['name'],'arguments':c['arguments'],'result':r}
              for c,r in observations(last_turn(messages)) if c['name'] in TOOLS and TOOLS[c['name']].changes_state]
    return {'kind':'selection_clarification','receipts':receipts}


def playback_state_step(route, messages, names):
    """Identify a missing observation before finalization, not a read limit.

    A receipt (including failure/unknown) prevents blind automatic retry. New
    reads selected by the model are handled independently by the runtime.
    """
    if not route.get('device') or route.get('control_actions') or 'get_playback_state' not in names:
        return None
    if any(call['name'] == 'get_playback_state' for call, result in observations(last_turn(messages))):
        return None
    return {'name': 'get_playback_state', 'arguments': {}}


def reply_step(route, messages, names):
    if any(g.get('page_request') for g in route.get('task_plan',{}).get('goals',[])):
        return page_delivery(route,messages)
    if (route.get('control_actions')
            or route.get('page_lookup', {}).get('requested')
            or route.get('episode_criteria', {}).get('requested')):
        return None
    if (route.get('response_needs',{}).get('acknowledgement_only')
            and not any(route.get(k) for k in ('podcast','web','device','library'))
            and not route.get('unsupported_request',{}).get('actions')
            and all(g['kind']=='general' for g in route.get('task_plan',{}).get('goals',[]))):
        goals=route.get('task_plan',{}).get('goals',[])
        specified=(goals[0].get('delivery') or {}).get('exact_text','') if len(goals)==1 else ''
        return {'kind':'acknowledgement','text':specified or '已记下。'}
    if route.get('library'):
        return None
    if (route.get('response_needs', {}).get('control_explanation') == 'cancel_sleep_timer'
            and not route.get('podcast') and not route.get('web') and not route.get('device')):
        return {'kind': 'cancel_timer_explanation'}
    request = route.get('unsupported_request', {})
    actions = request.get('actions', [])
    if actions and request.get('exclusive') and not set(actions) & names:
        return {'kind': 'unsupported', 'actions': list(dict.fromkeys(actions))}
    if actions:
        return None
    plan=route.get('task_plan',{}).get('goals',[])
    if (len(plan)==1 and plan[0]['kind']=='catalog_query' and not route.get('web') and not route.get('device')
            and not route.get('response_needs',{}).get('retrieval_statistics')):
        current=observations(last_turn(messages))
        selections=[(c,r) for c,r in current if c['name']=='list_episodes' and r.get('status')=='ok'
                    and not r.get('items') and (c['arguments'].get('position') or c['arguments'].get('episode_number'))]
        podcasts={i['id']:i['title'] for _,r in observations(messages) if r.get('status')=='ok'
                  for i in r.get('items',[]) if i.get('kind')=='podcast'}
        if len(selections)==1:
            call,result=selections[0];args=call['arguments'];title=podcasts.get(args['podcast_id'])
            if title:
                return {'kind':'selection','title':title,'number':args.get('position') or args['episode_number'],
                    'selector_kind':'title_number' if args.get('episode_number') else args.get('order','newest'),'result':result}
    if route.get('response_needs', {}).get('episode_page_listing'):
        pages = [(c, r) for c, r in observations(last_turn(messages)) if c['name'] == 'list_episodes']
        if len(pages) == 1 and pages[0][1].get('status') == 'ok':
            call, result = pages[0]
            if not call['arguments'].get('position') and not call['arguments'].get('episode_number'):
                return {'kind': 'episode_page', 'result': result,
                        'titles_only': route['response_needs'].get('titles_only', False)}
    # An introduction is a semantic answer. A short RSS body can also contain
    # ads and links; keep it as evidence for the existing introduction policy.
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


def library_plan(route):
    """A standalone library query is meaningful even without an active goal graph."""
    plan = route.get('task_plan', {}).get('goals', [])
    if len(plan) == 1 and plan[0]['kind'] == 'library_query':
        return plan
    return goals(route)


def library_route(route, progress):
    if progress is None or len(route.get('task_plan',{}).get('goals',[]))<=1:
        return route
    active=[g for g in goals(route) if g['kind']=='library_query' and ready(g,progress)
            and progress.get(g['id'],{}).get('status','pending') not in TERMINAL]
    if len(active)!=1:return route
    return {**route,'task_plan':{'goals':active},'control_actions':[],
            'web':False,'device':False,'podcast':False}


def library_completion_step(route, messages, progress=None):
    """Compute a fully observed library listing for the model's answer."""
    route=library_route(route,progress)
    plan=library_plan(route)
    if (len(plan)!=1 or plan[0]['kind']!='library_query'
            or route.get('web') or route.get('podcast') or route.get('device')):return None
    selection=library_completion_selection(route)
    rows=[(c,r) for c,r in goal_observations(last_turn(messages)) if c['name']=='query_library']
    if selection:
        rows=[(c,r) for c,r in rows if all(c['arguments'].get(k,'all' if k=='favorite_kind' else '')==v for k,v in selection.items())]
        results=[r for c,r in rows]
        if not results or any(r.get('status')!='ok' for r in results):return None
        step={'kind':'library','pages':sorted({r['offset']:r for r in results}.values(),key=lambda r:r['offset'])}
    else:step=library_reply_step(route,messages)
    if not step:return None
    pages=step['pages']
    # Exhaustion is established by the actual cursor, never a query quota.
    if pages[-1].get('has_more'):return None
    expected=pages[0]['offset']
    if expected and not route['response_needs'].get('last_library_page'):return None
    for page in pages:
        if page['offset']!=expected:return None
        expected+=len(page['items'])
    if expected!=pages[-1]['total']:return None
    completion=route['response_needs'].get('library_completion','all')
    if completion!='all':
        items=[item for page in pages for item in page['items']]
        if pages[0]['tab'] not in ('continue','history','favorites') or any(
                item.get('kind')!='episode' or type(item.get('completed')) is not bool for item in items):return None
        selected=[item for item in items if item['completed']==(completion=='completed')]
        step={**step,'completion':completion,'unfiltered_total':pages[0]['total'],
            'pages':[{**pages[0],'offset':0,'total':len(selected),'returned':len(selected),'items':selected,'has_more':False,'next_offset':0}]}
    elif selection and selection['tab']=='favorites' and selection['favorite_kind']=='all':
        items=[item for page in pages for item in page['items']]
        sections=[]
        for kind,label in (('podcast','收藏节目'),('episode','收藏分集')):
            selected=[item for item in items if item['kind']==kind]
            sections.append({'section_name':label,'pages':[{**pages[0],'offset':0,'total':len(selected),
                'returned':len(selected),'items':selected,'has_more':False,'next_offset':0}]})
        step={**step,'sections':sections}
    return {**step,'goal_id':plan[0]['id'],'proof_calls':[c['id'] for c,r in rows]}


def library_completion_selection(route):
    needs=route.get('response_needs',{});plan=library_plan(route)
    if (not needs.get('library_listing')
            or len(plan)!=1 or plan[0]['kind']!='library_query' or route.get('control_actions')
            or any(route.get(k) for k in ('web','podcast','device'))):return None
    favorites=needs.get('library_scope','all')=='favorites'
    completion=needs.get('library_completion','all')
    if completion=='all' and (not favorites or needs.get('last_library_page')):return None
    return {'tab':'favorites' if favorites else 'continue' if needs['library_completion']=='incomplete' else 'history',
        'favorite_kind':needs.get('library_kind','all') if completion=='all' else 'episodes' if favorites else 'all',
        'query':plan[0].get('query','')}


def library_completion_call(route,messages,progress=None):
    route=library_route(route,progress)
    selection=library_completion_selection(route)
    if not selection:return None
    rows=[(c,r) for c,r in goal_observations(last_turn(messages)) if c['name']=='query_library'
        and all(c['arguments'].get(k,'all' if k=='favorite_kind' else '')==v for k,v in selection.items())]
    if not rows:return {'name':'query_library','arguments':{**selection,'offset':0}}
    if any(r.get('status')!='ok' for c,r in rows):return None
    offsets={r['offset'] for c,r in rows};last=max((r for c,r in rows),key=lambda r:r['offset'])
    following=last.get('next_offset',0)
    if last.get('has_more') and following>last['offset'] and following not in offsets:
        return {'name':'query_library','arguments':{**selection,'offset':following}}
    return None


def waiting_selection_step(route,progress,messages,question):
    """One model-written question, with choices from the actual selection page."""
    plan=goals(route)
    waiting=[g for g in plan if progress.get(g['id'],{}).get('status')=='waiting_user']
    if (len(waiting)!=1 or waiting[0]['kind']!='control' or waiting[0].get('action')!='play_episode'
            or route.get('web') or route.get('library') or route.get('episode_criteria',{}).get('requested')
            or route.get('response_needs',{}).get('brief_episode_intro') or not question):return None
    if any(g['kind'] not in ('control','catalog_query') or
           (g['kind']=='catalog_query' and g['id'] not in waiting[0].get('depends_on',[])) for g in plan):return None
    rows=goal_observations(last_turn(messages))
    pages=[r for c,r in rows if c['name']=='list_episodes' and r.get('status')=='ok' and r.get('items')]
    if len(pages)!=1:return None
    receipts=[{'name':c['name'],'arguments':c['arguments'],'result':r} for c,r in rows if TOOLS[c['name']].changes_state]
    return {'kind':'waiting_selection','result':pages[0],'receipts':receipts,'question':question}
