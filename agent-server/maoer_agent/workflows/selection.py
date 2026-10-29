"""Selection evidence and confirmation policy; returns facts, not final replies."""
import hashlib
import json
import re
from maoer_agent.contracts.errors import ModelFailure
from maoer_agent.tools.definitions import same_arguments
from maoer_agent.memory.history import last_turn, PLAYBACK_SNAPSHOT_TOOLS
from maoer_agent.workflows.episode_pages import observations
from maoer_agent.context.route import episode_pages,displayed_episode_pages,displayed_selection


def normalize_selector(lookup, message, displayed_pages=()):
    """Apply the product's explicit numeric catalog grammar, not topic routing."""
    # A complete affirmative request clause can be followed by independent
    # constraints. Never search arbitrary negated/quoted prose for an ordinal.
    clause=re.split(r'[，,。；;！？!?]',message.strip(),maxsplit=1)[0].strip()
    simple=re.fullmatch(r'(?:(?:请|帮我)\s*)?(?:(?:介绍|查询|查找|查一下|查|看看|说说)\s*)?'
        r'(?:《(?P<podcast>[^》]{1,150})》\s*)?'
        r'第(?P<number>[0-9]{1,6}|[零〇一二两三四五六七八九十百千]+)(?P<unit>期|条|个|集)'
        r'(?:在讲什么|讲什么|的内容|是什么|的标题)?',clause)
    tail=message[len(clause):]
    if (simple and lookup.get('target_kind','none') in ('none','episode_selection')
            and not re.search(r'(?:我指|按|用)\s*(?:作者|标题|期号|EP)',tail,re.IGNORECASE)):
        value=simple['number']
        if value.isdecimal():number=int(value)
        else:
            digits=dict(zip('零〇一二两三四五六七八九',(0,0,1,2,2,3,4,5,6,7,8,9)))
            number=part=0
            for char in value:
                if char in digits:part=digits[char]
                else:number+=(part or 1)*{'十':10,'百':100,'千':1000}[char];part=0
            number+=part
        title=simple['podcast'] or lookup.get('podcast_title','')
        pages=[p for p in displayed_pages if not title or p.get('podcast_title')==title]
        if 0<number<=100000:
            if pages and simple['unit']!='集':
                lookup={**lookup,'target_kind':'episode_selection','selector_kind':'displayed_page',
                    'selector_value':number,'page_reference':pages[-1]['reference'],'podcast_title':title,
                    'episode_id':'','podcast_id':'','episode_title':''}
            elif simple['unit'] in ('集','期'):
                lookup={**lookup,'target_kind':'episode_selection','requested':False,
                    'selector_kind':'reverse_source' if simple['unit']=='集' else 'title_number',
                    'selector_value':number,'podcast_title':title}
    # A relative release is not an absolute catalog ordinal inferred by the router.
    # Keep the actual historical list available to the semantic planner instead.
    if (re.search(r'(?:上|前|下|后)一[期集]', message)
            and not re.search(r'(?:第|期号|原始列表)\s*[零一二三四五六七八九十百千0-9]+', message)
            and lookup.get('selector_kind') in ('reverse_source', 'source', 'newest', 'title_number')):
        lookup = {**lookup, 'selector_kind': 'none', 'selector_value': 0}
    if lookup.get('selector_kind') == 'displayed_page':
        bound=displayed_selection(lookup,displayed_pages)
        if bound:
            page,item=bound
            return {**lookup,'target_kind':'episode_selection','requested':False,
                    'page_reference':page['reference'],'episode_title':item['title'],
                    'podcast_title':page.get('podcast_title',''),'episode_id':item['id'],
                    'podcast_id':item['podcast_id'],'query':item['title']},False
        # Keep the unresolved relation for the existing clarification path.
        return lookup,False
    title_query = re.fullmatch(r'(?:请\s*)?(?:帮我\s*)?(?:查询|查找|找|查)\s*《(?P<podcast>[^》]{1,150})》'
        r'标题(?:包含|含)[“「"](?P<title>[^”」"]{1,100})[”」"](?:的)?(?:那集|分集)'
        r'(?:[，,。；;]\s*(?:先别播放|不要播放|不播放|先列出来让我选))*[。.!！]?', message.strip())
    if title_query:
        return {**lookup, 'target_kind': 'episode_selection', 'requested': False,
            'selector_kind': 'none', 'selector_value': 0, 'podcast_title': title_query['podcast'],
            'episode_title': title_query['title'], 'query': title_query['title'], 'exclusive': True, 'other_tasks': []}, True
    original = r'(?:(?:RSS)?原始(?:RSS)?(?:列表|顺序)|节目页原始列表)'
    phrase = (r'(?:标题(?:期号)?(?:第)?[0-9]{1,6}[期集]?|第[0-9]{1,6}期|'
              + original + r'?倒数第[0-9]{1,6}条?|'
              + original + r'第[0-9]{1,6}条|第[0-9]{1,6}集)')
    whole = re.fullmatch(r'(?:请\s*)?(?:帮我\s*)?(?:查询|查找|查一下|查|改查|找一下|定位|现在改要|这次我指的是)\s*'
        r'(?:(?:《(?P<podcast>[^》]{1,150})》|(?P<plain_podcast>[^，,。；;《》]{1,150}?))\s*)?(?P<selector>' + phrase + r')'
        r'(?:[，,。；;]\s*(?:先别播放|先不播放|不要播放|不播放))*[。.!！]?', message.strip(), re.IGNORECASE)
    if not whole:
        # Semantic selectors are already explicit. Do not extract another
        # meaning from a negated, quoted or explanatory part of the sentence.
        return lookup, False
    matched = re.fullmatch(phrase, whole['selector'], re.IGNORECASE)
    if not matched:return lookup, False
    text = matched[0]
    number = int(re.search(r'[0-9]+', text)[0])
    if not 1 <= number <= 100000:
        return lookup, False
    kind = ('title_number' if text.startswith('标题') or text.endswith('期') else
            'reverse_source' if '倒数' in text or text.endswith('集') else 'source')
    lookup = {**lookup, 'target_kind': 'episode_selection', 'requested': False,
              'selector_kind': kind, 'selector_value': number}
    if whole:
        lookup.update(podcast_title=whole['podcast'] or whole['plain_podcast'] or lookup['podcast_title'], exclusive=True, other_tasks=[])
    return lookup, whole is not None


def selection_step(route, messages, names):
    lookup = route.get('page_lookup', {})
    if lookup.get('target_kind')=='evidence_quote':
        # Old source numbers are answer-local. Acquire a new excerpt using only
        # an unambiguous episode identity observed in actual paired receipts.
        known={ (i['podcast_id'],i['id']):i for _,r in observations(messages) if r.get('status')=='ok'
                for i in r.get('items',[]) if i.get('kind')=='episode' and i.get('podcast_id') and i.get('id') }
        matches=[i for i in known.values() if
            (lookup.get('episode_id') and i['id']==lookup['episode_id'] and i['podcast_id']==lookup.get('podcast_id'))
            or (lookup.get('episode_title') and i.get('title')==lookup['episode_title']
                and (not lookup.get('podcast_title') or i.get('podcast_title')==lookup['podcast_title']))]
        if len(matches)==1 and 'read_episode_description' in names:
            item=matches[0];args={'podcast_id':item['podcast_id'],'episode_id':item['id'],'offset':0}
            if not any(c['name']=='read_episode_description' and same_arguments(c['name'],c['arguments'],args) for c,_ in observations(last_turn(messages))):
                return {'kind':'call','name':'read_episode_description','arguments':args}
        return None
    kind, number = lookup.get('selector_kind', 'none'), lookup.get('selector_value', 0)
    if lookup.get('target_kind') != 'episode_selection' or kind == 'none' or not number:
        return None
    if kind == 'displayed_page':
        bound=displayed_selection(lookup,episode_pages(messages)+displayed_episode_pages(messages))
        if not bound:
            return None  # Missing/ambiguous display context must be clarified by the model.
        page,item=bound
        current = observations(last_turn(messages))
        if route.get('response_needs', {}).get('brief_episode_intro'):
            args = {'podcast_id': item['podcast_id'], 'episode_id': item['id'], 'offset': 0}
            if 'read_episode_description' in names and not any(c['name'] == 'read_episode_description' and same_arguments(c['name'],c['arguments'],args) for c, _ in current):
                return {'kind': 'call', 'name': 'read_episode_description', 'arguments': args}
            return None
        if lookup.get('exclusive') and not route.get('control_actions'):
            return {'kind': 'selection', 'title': lookup.get('podcast_title', ''), 'selector_kind': kind,
                    'number': number, 'titles_only': route.get('response_needs', {}).get('titles_only', False),
                    'result': {'status': 'ok', 'items': [item]}}
        return None
    title = lookup.get('podcast_title', '').strip()
    if not title:
        return None
    current = observations(last_turn(messages))
    podcasts = {i['id'] for _, r in observations(messages) if r.get('status') == 'ok'
                for i in r.get('items', []) if i.get('kind') == 'podcast' and i.get('title', '').strip() == title}
    if not podcasts:
        if 'search_catalog' in names and not any(c['name'] == 'search_catalog' for c, _ in current):
            return {'kind': 'call', 'name': 'search_catalog', 'arguments': {'query': title}}
        return None
    if len(podcasts) != 1:
        return None
    args = {'podcast_id': next(iter(podcasts)), 'query': '', 'order': 'newest' if kind == 'title_number' else kind,
            'offset': 0, 'position': 0 if kind == 'title_number' else number,
            'episode_number': number if kind == 'title_number' else 0}
    for call, result in current:
        if call['name'] == 'list_episodes' and same_arguments(call['name'],call['arguments'],args):
            if lookup.get('exclusive'):
                return {'kind': 'selection', 'title': title, 'selector_kind': kind, 'number': number, 'result': result,
                        'titles_only': route.get('response_needs', {}).get('titles_only', False)}
            return None
    if 'list_episodes' in names:
        return {'kind': 'call', 'name': 'list_episodes', 'arguments': args}
    return None


def resolved_episode(route,messages):
    """The single queried episode stays the same subject during delivery."""
    plan=route.get('task_plan',{}).get('goals',[])
    if len(plan)!=1 or plan[0]['kind']!='catalog_query':return None
    needs=route.get('response_needs',{})
    if (route.get('episode_criteria',{}).get('requested') or needs.get('retrieval_statistics')
            or plan[0].get('page_request')):return None
    read={}
    for call,result in observations(last_turn(messages)):
        if call['name']!='read_episode_description' or result.get('status')!='ok':continue
        for item in result.get('items',[]):
            if (item.get('kind')=='episode' and item.get('id')==call['arguments'].get('episode_id')
                    and item.get('podcast_id')==call['arguments'].get('podcast_id') and item.get('evidence')):
                read[(item['podcast_id'],item['id'])]={k:item[k] for k in ('id','podcast_id','title','podcast_title') if k in item}
    return next(iter(read.values())) if len(read)==1 else None


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
        for call in (message.get('tool_calls') or []):
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
    prior_play_ids = {c['id'] for m in previous for c in (m.get('tool_calls') or [])
                      if c['function']['name'] == 'play_episode'}
    prior_play = any(m['role'] == 'tool' and m.get('tool_call_id') in prior_play_ids
                     and json.loads(m['content']).get('playing') is True for m in previous)
    if not prior_play:
        for message in previous:
            for call in (message.get('tool_calls') or []):
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
        for call in (message.get('tool_calls') or []):
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
