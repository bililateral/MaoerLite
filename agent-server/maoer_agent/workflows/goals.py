"""Goal readiness and receipt bindings. No I/O, model calls or API dependencies."""
from maoer_agent.contracts.intent import web_delivery, web_requirements, episode_description_required
from maoer_agent.workflows.introductions import description_items, description_call
from copy import deepcopy
from pydantic import ValidationError
from maoer_agent.tools.definitions import TOOLS
from maoer_agent.tools.results import receipt_evidence
from maoer_agent.memory.history import last_turn
from maoer_agent.workflows.episode_pages import active as page_workflow_active
from datetime import datetime,timezone,timedelta
from maoer_agent.core.urls import public_links
from maoer_agent.workflows.pagination import observed_page, page_continuation

TERMINAL={'satisfied','failed','unknown','unavailable','waiting_user','blocked'}
QUERY_KINDS={'public_query','catalog_query','library_query','general'}


def information_evidence_required(goal,route):
    if goal['kind'] in QUERY_KINDS-{'general'}:return True
    needs=route.get('response_needs',{})
    return (goal['kind']=='general' and goal.get('as_of')=='current'
            and not (needs.get('app_guidance') or needs.get('acknowledgement_only')))


def parameter_state(goal):
    """Separate an incomplete plan, a protocol error and an unsupported value."""
    action=goal.get('action','');args=goal.get('arguments',{})
    model=TOOLS[action].input_model
    if set(args)-set(model.model_fields):
        raise ValueError('Goal arguments must use contracted fields: '+', '.join(model.model_fields))
    try:
        model.model_validate(args)
    except ValidationError as error:
        kinds={e['type'] for e in error.errors()}
        if kinds <= {'missing'}:return 'incomplete'
        if kinds <= {'missing','string_too_short'} and all(e['type']=='missing' or e.get('input')=='' for e in error.errors()):return 'incomplete'
        if action in ('set_playback_speed','set_sleep_timer') and kinds <= {'literal_error','greater_than_equal','less_than_equal'}:
            return 'unsupported'
        raise ValueError('Goal argument types must match the control tool contract') from None
    return 'supported'


def observations(messages):
    return [({'id':row['call_id'],'name':row['tool'],'arguments':row['arguments']},row['result'])
            for row in receipt_evidence(messages)]


def goals(route):
    if page_workflow_active(route):return []
    plan=route.get('task_plan',{}).get('goals',[])
    if len(plan)==1 and plan[0]['kind']=='conversation_query':return plan
    if route.get('page_lookup',{}).get('selector_kind')=='displayed_page' and not route.get('page_lookup',{}).get('page_reference'):
        return plan
    if len(plan)==1 and plan[0].get('page_request'):return plan
    if len(plan)==1 and plan[0]['kind']=='public_query' and route.get('web_lookup',{}).get('requirements'):
        return plan
    if len(plan)==1 and web_delivery(route)=='results' and plan[0]['kind'] in {'public_query','general'}:
        return plan
    # Current information needs an observation even when it is the sole goal.
    # Historical recall keeps its existing evidence/memory path, without a read quota.
    if len(plan)==1 and plan[0].get('as_of')=='current' and plan[0]['kind'] in QUERY_KINDS|{'device_query'}:
        return plan if plan[0]['kind']!='general' or information_evidence_required(plan[0],route) else []
    if len(plan)==1 and plan[0]['kind']=='control' and plan[0].get('arguments'):
        try:
            if parameter_state(plan[0])=='unsupported':return plan
        except ValueError:return plan
    return plan if len(plan)>1 or any(g.get('depends_on') for g in plan) else []


def ready(goal,progress):
    states=[progress.get(i,{}).get('status','pending') for i in goal.get('depends_on',[])]
    return all(s=='satisfied' if goal.get('require_success') else s in TERMINAL for s in states)


def reconcile(route,previous,bindings,messages,control_blocked=False):
    if not goals(route):return previous
    progress=deepcopy(previous)
    rows={c['id']:(index,c,r) for index,(c,r) in enumerate(observations(last_turn(messages)))}
    for goal in goals(route):
        p=progress.setdefault(goal['id'],{'status':'pending','call_ids':[]})
        linked=[(identity,*rows[identity]) for identity,gid in bindings.items() if gid==goal['id'] and identity in rows]
        linked.sort(key=lambda row:row[1]);p['call_ids']=[row[0] for row in linked]
        if goal['kind'] in ('control','device_query') and linked:
            result=linked[-1][3];status=result.get('status','unknown')
            p['status']={'ok':'satisfied','error':'failed','unknown':'unknown','accepted':'unknown',
                         'confirmation_required':'waiting_user'}.get(status,'unknown')
        elif goal['kind'] in QUERY_KINDS and linked:
            if set(p['call_ids'])-set(p.get('reviewed_calls',[])):p['status']='observed'
            if episode_description_required(route,goal) and p['status']=='satisfied' and not description_items(messages,p['call_ids']):
                p.update(status='observed',reason='分集介绍尚无对应RSS说明证据')
            page=observed_page(goal,messages) if goal.get('page_request') and not episode_description_required(route,goal) and ready(goal,progress) else None
            if page and set(page['call_ids']).issubset(p['call_ids']):
                p.update(status='satisfied',proof_calls=page['call_ids'],reviewed_calls=list(p['call_ids']))
        if goal['kind']=='control' and p['status']=='pending':
            action=goal.get('action','');args=goal.get('arguments',{})
            selection=route.get('page_lookup',{})
            if action=='play_episode' and selection.get('selector_kind')=='displayed_page' and not selection.get('page_reference'):
                p.update(status='waiting_user',reason='缺少已展示的选集列表，需要用户指定节目或分集')
            if args and action in TOOLS:
                try:
                    if parameter_state(goal)=='unsupported':p.update(status='unavailable',reason='用户指定的参数不支持')
                except ValueError:p.update(status='failed',reason='内部任务参数不符合工具契约，未执行')
            if control_blocked:p.update(status='blocked',reason='此前控制效果未确认，后续控制未执行')
        if goal['kind']=='unsupported' and ready(goal,progress):
            p.update(status='unavailable',reason='当前助手不支持此操作')
        if goal.get('require_success') and any(progress.get(i,{}).get('status') in TERMINAL-{'satisfied'} for i in goal.get('depends_on',[])):
            p.update(status='blocked',reason='所需前置结果未成功，未执行此项')
    # A trailing state goal is an observation after its dependencies, not a
    # lifetime read quota. Later research invalidates its old observation.
    for goal in goals(route):
        if goal['kind']!='device_query' or not goal.get('depends_on'):continue
        p=progress[goal['id']]
        mine=max((rows[i][0] for i in p['call_ids']),default=-1)
        dependency=max((rows[i][0] for g in goal['depends_on'] for i in
                        progress.get(g,{}).get('call_ids',[])+progress.get(g,{}).get('proof_calls',[]) if i in rows),default=-1)
        if mine<=dependency and p['status']=='satisfied':p['status']='pending'
    return progress


def matches(goal,name):
    spec=TOOLS[name]
    return ((goal['kind']=='control' and goal.get('action')==name)
        or (goal['kind']=='device_query' and name=='get_playback_state')
        or (goal['kind']=='public_query' and spec.scope=='web')
        or (goal['kind']=='catalog_query' and spec.scope=='podcast')
        or (goal['kind']=='library_query' and name=='query_library')
        or (goal['kind']=='general' and not spec.changes_state and spec.scope in {'web','podcast','library'}))


def scope(route,progress,available,allowed):
    plan=goals(route)
    if not plan:return available,allowed
    active=[g for g in plan if progress.get(g['id'],{}).get('status','pending') not in TERMINAL and ready(g,progress)]
    query_ready=any(g['kind'] in QUERY_KINDS for g in active)
    ordered_state_waiting=any(g['kind']=='device_query' and progress.get(g['id'],{}).get('status','pending') not in TERMINAL and not ready(g,progress) for g in plan)
    finished=all(progress.get(g['id'],{}).get('status') in TERMINAL for g in plan)
    names=set()
    for name in allowed:
        if any(matches(g,name) for g in active):names.add(name)
        elif not TOOLS[name].changes_state:
            if finished:names.add(name)
            elif query_ready and (name!='get_playback_state' or not ordered_state_waiting):names.add(name)
            elif any(g['kind']=='control' and g.get('action')=='play_episode' for g in active) and TOOLS[name].scope in ('podcast','device'):
                names.add(name)
    # No pending ordered state goal blocks a new, model-selected state read.
    if route.get('device') and not any(g['kind']=='device_query' and progress.get(g['id'],{}).get('status','pending') not in TERMINAL and not ready(g,progress) for g in plan):
        names.add('get_playback_state')
    names &= allowed
    if web_delivery(route)=='results':names.discard('read_web_page')
    return [d for d in available if d['function']['name'] in names],names


def bind(route,progress,previous,calls):
    bindings=dict(previous);assigned=set()
    for call in calls:
        choices=[g for g in goals(route) if matches(g,call['name']) and ready(g,progress)
                 and (not TOOLS[call['name']].changes_state or progress.get(g['id'],{}).get('status','pending') not in TERMINAL)
                 and (g['kind']!='control' or g['id'] not in assigned)]
        choices.sort(key=lambda g:(progress.get(g['id'],{}).get('status') in TERMINAL,g['kind']=='general'))
        if (not choices or progress.get(choices[0]['id'],{}).get('status') in TERMINAL) and not TOOLS[call['name']].changes_state and call['name']!='get_playback_state':
            queries=[g for g in goals(route) if g['kind'] in QUERY_KINDS-{'general'} and ready(g,progress)
                     and progress.get(g['id'],{}).get('status','pending') not in TERMINAL]
            if len(queries)==1:choices=queries
        if choices:
            bindings[call['id']]=choices[0]['id'];assigned.add(choices[0]['id'])
    return bindings


def initial_call(route,progress,names,messages=()):
    for kind in ('control','device_query'):
        for goal in goals(route):
            p=progress.get(goal['id'],{})
            if goal['kind']!=kind or p.get('status','pending')!='pending' or p.get('call_ids') or not ready(goal,progress):continue
            name=goal.get('action') if kind=='control' else 'get_playback_state'
            if name not in names:continue
            if not TOOLS[name].input_model.model_fields:return {'name':name,'arguments':{}}
            if name in ('set_playback_speed','set_sleep_timer') and parameter_state(goal)=='supported':
                return {'name':name,'arguments':TOOLS[name].input_model.model_validate(goal.get('arguments',{})).model_dump()}
    for goal in goals(route):
        p=progress.get(goal['id'],{})
        if ready(goal,progress) and p.get('status') not in TERMINAL:
            step=description_call(route,goal,messages,p.get('call_ids',[]),names)
            if step:return step
    if 'list_episodes' in names:
        for goal in goals(route):
            if goal.get('page_request') and not episode_description_required(route,goal) and ready(goal,progress) and progress.get(goal['id'],{}).get('status') not in TERMINAL:
                step=page_continuation(goal,messages)
                if step:return step
    return None


def missing_query(route,progress,names):
    if not route.get('web') or 'web_search' not in names:return None
    if any(g['kind']=='public_query' and progress.get(g['id'],{}).get('call_ids') for g in goals(route)):
        return None  # The finalizer can reuse evidence or select the missing target.
    for goal in goals(route):
        p=progress.get(goal['id'],{})
        if goal['kind']=='public_query' and ready(goal,progress) and not p.get('call_ids'):
            query=goal.get('query') or route.get('web_lookup',{}).get('query') or goal['request']
            return {'name':'web_search','arguments':{'query':query[:300],'delivery':web_delivery(route)}}
    return None


def view(route,progress):
    return [{**g,'progress':progress.get(g['id'],{'status':'pending'}),'ready':ready(g,progress),
        'information_evidence_required':information_evidence_required(g,route)} for g in goals(route)]


def reviewed(route,progress,assessments,sources,messages,allowed,blocks=None,memory_proofs=None):
    plan=goals(route)
    if not plan:return progress
    assessments={a.id:a for a in assessments}
    if set(assessments)!={g['id'] for g in plan}:raise ValueError('Every user goal needs an assessment')
    result=deepcopy(progress);numbers={s['number'] for s in sources}
    observed=observations(last_turn(messages))
    calls={c['id']:r for c,r in observed}
    information_reads={c['id'] for c,r in observed if r.get('status')=='ok'
        and not TOOLS[c['name']].changes_state and TOOLS[c['name']].scope in {'web','podcast','library'}}
    today=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    for goal in plan:
        p=result[goal['id']];a=assessments[goal['id']]
        if any(n not in numbers for n in a.sources) or any(i not in calls for i in a.call_ids):
            raise ValueError('Goal proof must reference actual current evidence')
        if goal['kind'] in ('control','device_query'):
            if a.status=='satisfied' and p['status']!='satisfied':raise ValueError('An assessment cannot invent execution')
            if (a.status=='waiting' and p['status']=='pending' and ready(goal,result)
                    and goal['kind']=='control' and parameter_state(goal)=='incomplete'):
                p.update(status='waiting_user',reason=a.note or '需要用户补充控制参数')
            continue
        if p['status'] in TERMINAL:continue
        if not ready(goal,result):
            if a.status not in ('waiting','needs_evidence'):raise ValueError('A dependent goal cannot complete before its prerequisite')
            continue
        if a.status=='satisfied':
            if goal['kind']=='conversation_query':
                if not a.memory_ids or not set(a.memory_ids).issubset(memory_proofs or {}):
                    raise ValueError('Conversation recall needs actual user statements or stored summary')
            if goal.get('page_request') and not episode_description_required(route,goal) and not observed_page(goal,messages):
                p.update(status='observed',reason='尚未取得用户目标页的匹配回执')
                continue
            if episode_description_required(route,goal):
                body_numbers={item['citation'] for item in description_items(messages,p.get('call_ids',[]))}
                delivered={n for block in (blocks or []) for n in block.get('sources',[])}
                if not body_numbers.intersection(a.sources).intersection(delivered):
                    p.update(status='observed',reason='分集介绍尚无对应RSS说明及交付引用',reviewed_calls=list(p.get('call_ids',[])))
                    continue
            if (goal['kind']=='public_query' and route.get('web_lookup',{}).get('requirements')
                    and web_requirements(route)['factual_answer']):
                delivered={n for b in (blocks or []) for n in b.get('sources',[])}
                body_numbers={item.get('citation') for c,r in observed
                    if c['name']=='read_web_page' and c['id'] in p.get('call_ids',[])
                    and c['id'] in a.call_ids and r.get('status')=='ok'
                    for item in r.get('items',[]) if item.get('document_id') and item.get('evidence')}
                if not set(a.sources)&delivered&body_numbers:
                    p.update(status='observed',reason='事实目标尚无对应正文回执及交付引用',reviewed_calls=list(p.get('call_ids',[])))
                    continue
            if web_delivery(route)=='results' and goal['kind'] in {'public_query','general'}:
                required=max(1,goal.get('requested_results',0))
                if required:
                    delivered={n for b in (blocks or []) for n in b.get('sources',[])}
                    matched={item.get('citation'):item.get('source_url') for c,r in observed
                        if c['name']=='web_search' and c['id'] in p.get('call_ids',[]) and r.get('status')=='ok'
                        for item in r.get('items',[]) if item.get('search_match',{}).get('relation') in {'primary','section'}}
                    delivered_urls={matched[n] for n in set(a.sources)&delivered&set(matched)
                        if any(n in b.get('sources',[]) and matched[n] in public_links(b.get('text','')) for b in (blocks or []))}
                    if len(delivered_urls)<required:
                        p.update(status='observed',reason=f'已交付合格搜索结果{len(delivered_urls)}条，用户要求{required}条',reviewed_calls=list(p.get('call_ids',[])))
                        continue
            if information_evidence_required(goal,route) and not (a.sources or information_reads.intersection(a.call_ids)):
                raise ValueError('A current or query information goal needs actual information evidence; control/state receipts are not knowledge evidence')
            local_reads={c['id'] for c,r in observed if r.get('status')=='ok'
                and c['name'] in allowed and TOOLS[c['name']].scope in {'podcast','library'} and not TOOLS[c['name']].changes_state}
            current_local_proof=bool(local_reads.intersection(a.call_ids).intersection(p.get('call_ids',[]))) and not route.get('web')
            if (information_evidence_required(goal,route) and web_delivery(route)!='results'
                    and goal['kind'] in ('public_query','general') and goal.get('as_of')=='current'
                    and not current_local_proof and a.through_date!=today):
                raise ValueError('Historical evidence does not complete a current-state goal')
            p.update(status='satisfied',proof_calls=a.call_ids,source_ids=a.sources,memory_ids=a.memory_ids,reviewed_calls=list(p.get('call_ids',[])))
        elif a.status=='needs_evidence':p['status']='observed'
        elif a.status=='unavailable':
            if goal['kind'] in QUERY_KINDS and any(matches(goal,name) for name in allowed) and not p.get('call_ids'):
                raise ValueError('An unattempted authorized query is still pending')
            if (goal['kind']=='public_query' and web_delivery(route)!='results' and goal.get('as_of')=='current'
                    and allowed & {'web_search','read_web_page'} and a.through_date!=today):
                raise ValueError('A current information goal still needs a current inquiry')
            p.update(status='unavailable',reason=a.note,reviewed_calls=list(p.get('call_ids',[])))
        elif a.status=='waiting':raise ValueError('A ready goal cannot silently disappear')
    return result


def complete(route,progress):
    return all(progress.get(g['id'],{}).get('status') in TERMINAL for g in goals(route))


def call_goal(route,bindings,identity):
    plan=route.get('task_plan',{}).get('goals',[])
    return next((g for g in plan if g['id']==bindings.get(identity)),plan[0] if len(plan)==1 and plan[0]['kind']=='public_query' else {})
