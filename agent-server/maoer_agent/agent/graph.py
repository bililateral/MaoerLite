"""LangGraph nodes; the durable device boundary and checkpoint names are unchanged."""
from maoer_agent.contracts.intent import web_delivery, web_source_links
import asyncio
import json
import time
from datetime import datetime,timezone,timedelta
from dataclasses import replace
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import set_config_context
from maoer_agent.contracts.errors import ModelFailure
from maoer_agent.core.errors import error_message
from maoer_agent.tools.definitions import validated_calls
from maoer_agent.memory.history import last_turn, remember, replay_references
from maoer_agent.memory.summary import MAX_BATCHES, SUMMARY_TIMEOUT, batch, build_summary, SummaryFormatError
from maoer_agent.workflows.selection import episode_selection_question, selection_step, resolved_episode
from maoer_agent.workflows.planning import prepare_calls, tool_scope
from maoer_agent.agent.state import State, MAX_TOOL_CALLS, MAX_MODEL_ROUNDS
from maoer_agent.workflows.intent import classify, allowed_tools, DEVICE_TOOLS
from maoer_agent.tools.definitions import SERVER_TOOLS, TOOLS
from maoer_agent.research.sources import annotate, valid_citations
from maoer_agent.tools.results import checked_result, receipt_evidence
from maoer_agent.context.assembler import AnswerInput, ContextAssembler, ordinary_question, introduction_ready
from maoer_agent.context.conversation import history_metrics
from maoer_agent.contracts.answers import QuotedAnswer, quotation_evidence
from maoer_agent.contracts.model import JsonMessages, model_call_context
from maoer_agent.contracts.tasks import checked_reply
from maoer_agent.contracts.execution import ResearchContext, PageView
from maoer_agent.core.limits import MAX_MODEL_ATTEMPTS
from maoer_agent.workflows import episode_pages
from maoer_agent.workflows.recommendations import criteria_call, duration_candidates
from maoer_agent.workflows import literal_listings
from maoer_agent.workflows.recovery import recovery_scope, recovered_selection_reply, remaining_controls
from maoer_agent.workflows.query_replies import reply_step, library_reply_step, library_completion_step, library_completion_call, playback_state_step, unsupported_setting_step, control_receipt_step, selection_clarification_step
from maoer_agent.workflows.web import evidence_step, evidence_scope, lookup_step, known_urls
from maoer_agent.research.evidence import answer_sources
from maoer_agent.workflows import goals as task_goals


class AgentGraph:
    def __init__(self, settings, store, model, get_run, research, catalog, page_renderer, query_renderer):
        self.settings, self.store, self.model, self.get_run = settings, store, model, get_run
        self.research = research
        self.catalog = catalog
        self.page_renderer = page_renderer
        self.query_renderer = query_renderer
        self.context = ContextAssembler()
        self.feed_urls = frozenset(i['feed_url'] for i in catalog)

    async def invoke_model(self, run_id, purpose, settings, messages, tools, publish):
        run = self.get_run(run_id)
        usage = dict(run.get('model_attempts', {}))
        if sum(usage.values()) >= MAX_MODEL_ATTEMPTS:
            raise ModelFailure('model_attempt_budget_exhausted')
        usage[purpose] = usage.get(purpose, 0) + 1
        self.store.update(run_id, model_attempts=usage)
        if purpose in {'intent','recovery_intent','quote_selection'}:
            messages=JsonMessages(messages)
        token=model_call_context.set({'run_id':run_id,'purpose':purpose,'attempt':usage[purpose]})
        try:
            return await self.model(settings, messages, tools, publish)
        finally:
            model_call_context.reset(token)

    def record_result(self, run_id, receipt, call=None):
        run = self.get_run(run_id)
        # Preserve raw receipt before optional annotation. Device fingerprints are separate.
        raw = dict(run.get('raw_results', {}))
        raw.setdefault(receipt['call_id'], receipt['result'])
        self.store.update(run_id, raw_results=raw)
        result = checked_result(call, raw[receipt['call_id']]) if call else receipt['result']
        if receipt['call_id'] in run.get('recovered_playback_calls', []):
            result = {**result, 'observation_scope': 'execution_before_process_restart'}
        try:
            result, sources = annotate(run, result)
        except (ValueError, TypeError, KeyError, OverflowError, OSError):
            # An enrichment failure cannot undo/replay a successfully observed operation.
            sources = run.get('sources', [])
            warnings = dict(run.get('result_warnings', {}))
            warnings[receipt['call_id']] = 'source_annotation_failed'
            self.store.update(run_id, result_warnings=warnings)
        receipt = {**receipt, 'result': result}
        results = list(run['results'])
        index = next((i for i, r in enumerate(results) if r['call_id'] == receipt['call_id']), None)
        if index is None:
            results.append(receipt)
        else:
            results[index] = receipt
        self.store.update(run_id, results=results, sources=sources)
        return receipt

    def compile(self, saver):
        graph = StateGraph(State)
        graph.add_node('prepare', self.prepare_node)
        graph.add_node('model', self.model_node)
        graph.add_node('device', self.device_node)
        graph.add_node('research', self.research_node)
        graph.add_edge(START, 'prepare')
        graph.add_edge('prepare', 'model')
        graph.add_conditional_edges('model', lambda state: 'device' if state['calls'] else 'research' if state.get('research_plan') else
                                    ('model' if state.get('continue_model') or state['messages'][-1]['role'] == 'tool' else END))
        graph.add_conditional_edges('research', lambda state: 'device' if state['calls'] else 'model')
        graph.add_conditional_edges('device', lambda state: 'research' if state.get('research_plan') else 'model')
        return graph.compile(checkpointer=saver)


    async def prepare_node(self, state):
        """Archive evicted turns once, before this task can dispatch any tools."""
        run_id = state['run_id']
        conversation = self.get_run(run_id)['conversation_id']
        saved = self.store.summary(conversation)
        candidates = state.get('summary_candidates', [])
        covered = set(saved['covered'])
        pending = [self.store.get(i) for i in candidates if i not in covered]
        failure = ''
        failure_details = {}
        async def discard(_):
            pass  # Summary text must never be streamed as the assistant's answer.
        for _ in range(MAX_BATCHES):
            if not pending:
                break
            self.store.update(run_id, status='summarizing', phase='summary', summary_turns=len(saved['covered']),
                              summary_pending=len(pending))
            ids, records = batch(pending)
            try:
                async def summarize(messages):
                    return await self.invoke_model(run_id, 'summary', replace(self.settings, agent_thinking=False),
                                                   messages, [], discard)
                data = await asyncio.wait_for(build_summary(summarize, saved['data'], records), SUMMARY_TIMEOUT)
            except (ModelFailure, ValueError, asyncio.TimeoutError) as exc:
                # Preserve the last good summary and its cursor. No recursive
                # retry or lost coverage: the next user task can try again.
                failure = exc.code if isinstance(exc, (ModelFailure, SummaryFormatError)) else 'summary_timeout'
                failure_details = {'kind': type(exc).__name__, 'code': failure}
                break
            saved = {'data': data, 'covered': saved['covered'] + ids}
            self.store.save_summary(conversation, saved)
            pending = pending[len(ids):]
        self.store.update(run_id, status='generating', summary_turns=len(saved['covered']),
                          summary_pending=len(pending), summary_error=failure, summary_error_details=failure_details)
        extra = {}
        if state.get('research'):
            self.store.update(run_id, phase='intent')
            async def route_model(settings, messages, tools, publish):
                return await self.invoke_model(run_id, 'intent', settings, messages, tools, publish)
            conversation_view = self.conversation_input(state, saved['data'], len(pending), saved['covered'])
            route = await classify(route_model, self.settings, state['messages'], self.catalog if 'search_hints' in self.get_run(run_id)['input'].get('capabilities', []) else [], conversation=conversation_view)
            extra['route'] = route
            self.store.update(run_id, search_hint=route['hint'], route=route, sources=[], conversation_context=history_metrics(conversation_view))
        return {'summary': saved['data'], 'summary_pending': len(pending), 'summary_covered': saved['covered'], 'summary_candidates': [], **extra}

    def conversation_input(self, state, summary=None, pending=None, covered=None):
        return self.context.conversation(state['messages'],state.get('memory',{}),
            state.get('summary',{}) if summary is None else summary,
            state.get('summary_pending',0) if pending is None else pending,
            state.get('history_turn_ids',()),state.get('summary_covered',()) if covered is None else covered)


    def answer_input(self, state, available, final_only):
        progress=self.get_run(state['run_id']).get('goal_progress',{})
        route=task_goals.answer_route(state.get('route',{}),progress)
        active_goals=[g for g in task_goals.goals(route) if task_goals.ready(g,progress)
                      and progress.get(g['id'],{}).get('status') not in task_goals.TERMINAL]
        response_goals=[g for g in active_goals if g['kind'] in task_goals.QUERY_KINDS|{'conversation_query'}]
        response_request=('\n'.join(g['request'] for g in response_goals)
            if len(task_goals.goals(route))>1 and response_goals and len(response_goals)==len(active_goals) else '')
        route={**route,'response_goals':response_goals,'response_request':response_request}
        episode=resolved_episode({**route,'task_plan':{'goals':response_goals}} if response_goals else route,
                                 state['messages'])
        scoped_episode=episode if (introduction_ready(route,state['messages']) or
            route.get('page_lookup',{}).get('target_kind')=='evidence_quote') else None
        sources=answer_sources(self.get_run(state['run_id']).get('sources', []),last_turn(state['messages']),
            web_delivery(route),(scoped_episode or {}).get('id',''))
        if route.get('page_lookup',{}).get('target_kind')=='evidence_quote':
            read_numbers={item['citation'] for r in receipt_evidence(last_turn(state['messages']))
                if r['tool'] in {'get_podcast_details','read_episode_description','read_web_page','search_rss'}
                and r['result'].get('status')=='ok' for item in r['result'].get('items',[])
                if item.get('citation') and item.get('evidence')}
            sources=[s for s in sources if s['number'] in read_numbers]
        if (response_goals and all(g['kind'] in {'general','conversation_query'}
                and not task_goals.information_evidence_required(g,route)
                and not progress.get(g['id'],{}).get('call_ids') for g in response_goals)
                and not web_source_links(route)):
            sources=[]  # Prior query IDs do not cite an unread ordinary-response goal.
        return AnswerInput(state['messages'], state.get('memory', {}), state.get('summary', {}),
            state.get('summary_pending', 0), sources,
            {**route,'goal_progress':progress,'goal_view':task_goals.view(route,progress),
             'resolved_episode':episode,'saved_replies':self.get_run(state['run_id']).get('reply_parts',{}),
             'business_context':state.get('business_context',{}),
             'execution_results':[r for r in receipt_evidence(last_turn(state['messages']))
                 if r['tool'] in TOOLS and TOOLS[r['tool']].changes_state
                 and r['call_id'] not in self.get_run(state['run_id']).get('reported_controls',[])]}, available, state.get('extended_episodes', False),
            state.get('research', False), final_only, self.settings.model,
            self.settings.context_budget_bytes, self.settings.agent_thinking, self.conversation_input(state))

    def scoped_tools(self,state,final_only):
        run=self.get_run(state['run_id']);route=state.get('route',{})
        available,allowed=tool_scope(route,run['input'].get('capabilities',[]),state.get('extended_episodes',False),state.get('research',False),final_only)
        available,allowed=recovery_scope(run,route,available,allowed)
        available,allowed=evidence_scope(state['messages'],available,allowed,web_delivery(route))
        return task_goals.scope(route,run.get('goal_progress',{}),available,allowed)

    async def answer_message(self, state, available, final_only):
        run_id = state['run_id']
        request = self.answer_input(state, available, final_only)
        prepared = self.context.answer(request)
        messages, metrics = prepared.messages, prepared.metrics
        self.store.update(run_id, context_budget=metrics)
        started, waited = time.monotonic(), 0
        async def publish(text):
            if (ordinary_question(state.get('route', {})) and not request.sources
                    and not any(task_goals.information_evidence_required(g,state.get('route',{}))
                                for g in task_goals.goals(state.get('route',{})))):
                self.store.update(run_id, text=self.saved_reply(state,text), status='generating', retry_after=0)
        for attempt in range(3):
            self.store.update(run_id, text=self.failure_reply(state), status='generating', retry_after=0, error='', error_detail={},
                              phase='model_analysis' if state.get('tool_count', 0) else 'model_planning')
            try:
                message = await self.invoke_model(run_id, 'main', self.settings,
                                           messages, [] if final_only else available, publish)
                break
            except ModelFailure as exc:
                if (not exc.retryable or attempt == 2 or waited + exc.delay > 120
                        or time.monotonic() - started + exc.delay >= 180):
                    raise
                self.store.update(run_id, status='retry_wait', retry_after=exc.delay, error=exc.code,
                                  retry_at=int(time.time() + exc.delay))
                await asyncio.sleep(exc.delay)
                waited += exc.delay
        return message, messages

    async def quote_reply(self, state, available, correction=None):
        request = self.answer_input(state, [], True)
        feedback = correction if correction is not None else self.get_run(state['run_id']).get('answer_feedback')
        messages = self.context.quote_selection(request, self.query_renderer, feedback)
        source_numbers = {source['number'] for source in request.sources}
        async def discard(text):
            pass  # Composition JSON and unverified text never enter the UI.
        for attempt in range(2):
            self.store.update(state['run_id'], phase='model_analysis', text=self.saved_reply(state))
            try:
                reply = await self.invoke_model(state['run_id'], 'quote_selection', self.settings, messages, [], discard)
            except ModelFailure as exc:
                if exc.code != 'incomplete_model_response' or exc.details.get('finish_reason') != 'length':
                    raise
                failures = list(self.get_run(state['run_id']).get('answer_delivery_failures', []))
                failures.append({'kind': exc.code, 'details': exc.details, 'purpose': 'quote_selection'})
                self.store.update(state['run_id'], answer_delivery_failures=failures)
                if attempt:
                    raise
                continue
            try:
                if reply.get('tool_calls'):
                    raise ValueError('Delivery cannot execute tools')
                text = (reply.get('content') or '').strip()
                if text.startswith('```'):
                    text = text.split('\n', 1)[1].rsplit('```', 1)[0]
                delivery = QuotedAnswer.model_validate_json(text)
                quotes=quotation_evidence(request.sources) if request.route.get('page_lookup',{}).get('target_kind')=='evidence_quote' else None
                blocks = delivery.checked_blocks(source_numbers, local_citations=True, episode=request.route.get('resolved_episode'),quote_refs=quotes)
                answer = self.query_renderer({'kind': 'factual_answer', 'blocks': blocks,
                    'sources': request.sources, 'link_sources': web_source_links(request.route), 'include_source_links': web_source_links(request.route)})
                if not valid_citations(answer, request.sources):
                    raise ValueError('Invalid inline source')
                checked_reply(answer, request.route)
                self.store.update(state['run_id'], answer_delivery=delivery.model_dump(exclude_none=True))
                break
            except (ValueError, TypeError, KeyError) as error:
                failures = list(self.get_run(state['run_id']).get('answer_delivery_failures', []))
                failures.append({'kind': type(error).__name__, 'reason': str(error), 'content': reply.get('content') or ''})
                self.store.update(state['run_id'], answer_delivery_failures=failures)
                if attempt:
                    raise ModelFailure('invalid_citation') from None
                messages = [messages[0], {'role': 'system', 'content':
                    '上次交付未符合JSON或来源契约。按具体错误修正正文块及实际可用编号，不执行工具。'
                    +json.dumps({'contract_error':[
                        {'path':list(item['loc']),'type':item['type']}
                        for item in error.errors(include_url=False,include_input=False)]
                        if hasattr(error,'errors') else str(error)},ensure_ascii=False)}, *messages[1:]]
        message = {'role': 'assistant', 'content': answer}
        return message

    def execution_reply(self, state):
        receipts={row['call_id']:row for row in receipt_evidence(last_turn(state['messages']))
                  if row['tool'] in TOOLS and TOOLS[row['tool']].changes_state}
        if not receipts:return ''
        return self.query_renderer({'kind':'control_receipts','receipts':[
            {'name':r['tool'],'arguments':r['arguments'],'result':r['result']} for r in receipts.values()],
            'unsupported':state.get('route',{}).get('unsupported_request',{}).get('actions',[])})

    def saved_reply(self, state, text=''):
        parts=list(self.get_run(state['run_id']).get('reply_parts',{}).values())
        for part in (text,):
            if part and part not in parts:parts.append(part)
        return '\n\n'.join(parts)

    def failure_reply(self,state):
        return self.saved_reply(state,self.execution_reply(state))

    def save_reply(self, state, text, identities):
        if not text or not identities:return
        parts=dict(self.get_run(state['run_id']).get('reply_parts',{}))
        parts[':'.join(identities)]=text
        self.store.update(state['run_id'],reply_parts=parts)


    async def page_context(self, state, names, final_only):
        run_id = state['run_id']
        async def discard(_): pass
        while True:
            run = self.get_run(run_id)
            step = episode_pages.next_step(PageView(state['route'], state['messages'],
                run.get('page_checks', {}), run.get('sources', []), self.feed_urls), names, final_only)
            if step['kind'] == 'verify':
                self.store.update(run_id, phase='read_web_page')
                try:
                    verdict = await self.invoke_model(run_id, 'page_identity',
                        replace(self.settings, agent_thinking=False),
                        episode_pages.match_messages(state['route']['page_lookup'], step['item'],
                            step.get('publisher_transcript_link', False)), [], discard)
                    matched = episode_pages.parse_match(verdict)
                except (ValueError, TypeError, KeyError, AttributeError):
                    raise ModelFailure('invalid_page_identity') from None
                checks = dict(run.get('page_checks', {}))
                checks[step['key']] = matched
                self.store.update(run_id, page_checks=checks)
                continue
            if step['kind'] == 'answer':
                step['presentation']['target_kind'] = state['route']['page_lookup'].get('target_kind', 'episode_page')
                self.store.update(run_id, page_resolution=step['resolution'])
            return step

    async def business_context(self,state,names,final_only,progress):
        route=state.get('route',{});messages=state['messages']
        selected=selection_step(route,messages,names) if state.get('extended_episodes') else None
        result=(literal_listings.completion(route,messages)
                or library_completion_step(route,messages,progress)
                or duration_candidates(route,messages)
                or selection_clarification_step(route,progress,messages)
                or control_receipt_step(route,messages)
                or unsupported_setting_step(route,progress)
                or recovered_selection_reply(route,messages)
                or (selected if selected and selected['kind']=='selection' else None)
                or reply_step(route,messages,names)
                or library_reply_step(route,messages)
                or task_goals.failure_step(route,progress,messages))
        next_step=None if final_only else (task_goals.initial_call(route,progress,names,messages)
            or (library_completion_call(route,messages,progress) if 'query_library' in names else None)
            or literal_listings.next_call(route,messages,names)
            or criteria_call(route,messages,names)
            or (selected if selected and selected['kind']!='selection' else None))
        if state.get('research') and episode_pages.active(route):
            page=await self.page_context(state,names,final_only)
            if page['kind']=='answer':result=page
            else:next_step=page
        if not next_step and not final_only and state.get('research') and not episode_pages.active(route):
            next_step=(task_goals.missing_query(route,progress,names)
                or playback_state_step(route,messages,names)
                or lookup_step(route,messages,names)
                or evidence_step(messages,names,web_delivery(route)))
        if self.get_run(state['run_id']).get('playback_refresh_pending') and not final_only:
            next_step={'name':'get_playback_state','arguments':{}}
        return {'result':result,'next_step':next_step}


    async def model_node(self, state):
        if state['rounds'] >= MAX_MODEL_ROUNDS:
            raise ModelFailure('tool_round_limit')
        run_id = state['run_id']
        final_only = state.get('final_only', False) or state.get('tool_count', 0) >= MAX_TOOL_CALLS
        run = self.get_run(run_id)
        if (run.get('recovered_playback_calls') and not run.get('playback_refresh_pending')
                and 'control_actions' not in state.get('route', {})):
            # Upgrade a legacy checkpoint once, retaining its existing route.
            async def recovery_intent(settings, messages, tools, publish):
                return await self.invoke_model(run_id, 'recovery_intent', settings, messages, tools, publish)
            checked = await classify(recovery_intent, self.settings, state['messages'], [], conversation=self.conversation_input(state))
            route = {**state.get('route', {}), 'control_actions': checked['control_actions']}
            state = {**state, 'route': route}
            self.store.update(run_id, route=route)
        run=self.get_run(run_id)
        # Older checkpoints did not bind some standalone goals. Reuse only
        # dispatched calls with actual receipts; never create or replay a call.
        received={r['call_id'] for r in receipt_evidence(last_turn(state['messages']))}
        unbound=[c for c in run.get('tool_ledger',[])
                 if c['id'] in received and c['id'] not in run.get('goal_bindings',{})]
        if unbound:
            bindings=task_goals.bind(state.get('route',{}),run.get('goal_progress',{}),
                run.get('goal_bindings',{}),unbound,state['messages'])
            if bindings!=run.get('goal_bindings',{}):
                self.store.update(run_id,goal_bindings=bindings)
                run=self.get_run(run_id)
        progress=task_goals.reconcile(state.get('route',{}),run.get('goal_progress',{}),run.get('goal_bindings',{}),state['messages'],run.get('control_blocked',False))
        if progress!=run.get('goal_progress',{}):self.store.update(run_id,goal_progress=progress)
        available,allowed=self.scoped_tools(state,final_only)
        closed=task_goals.close_failed_queries(state.get('route',{}),progress,state['messages'],allowed,run.get('sources',[]))
        if closed!=progress:
            progress=task_goals.reconcile(state.get('route',{}),closed,run.get('goal_bindings',{}),state['messages'],run.get('control_blocked',False))
            self.store.update(run_id,goal_progress=progress)
            available,allowed=self.scoped_tools(state,final_only)
        names = {d['function']['name'] for d in available}
        view=await self.business_context(state,names,final_only,progress)
        state={**state,'business_context':view}
        direct=view['result']
        plan=task_goals.goals(state.get('route',{}))
        quote_selected=(state.get('route',{}).get('page_lookup',{}).get('target_kind')=='evidence_quote'
                        and not view['next_step'] and bool(self.answer_input(state,[],True).sources))
        if quote_selected:
            message=await self.quote_reply(state,available)
            messages=state['messages']
        else:
            message,messages=await self.answer_message(state,available,final_only)
        try:
            calls = validated_calls(message, state.get('extended_episodes', False))
        except (ValueError, KeyError, TypeError):
            raise ModelFailure('invalid_tool_call') from None
        if final_only and calls:
            raise ModelFailure('tool_round_limit')
        if 'web_lookup' in state.get('route',{}):
            for call in calls:
                if call['name']=='web_search':
                    call['arguments']['delivery']=web_delivery(state['route'])
                    native=next(c for c in message['tool_calls'] if c['id']==call['id'])
                    native['function']['arguments']=json.dumps(call['arguments'],ensure_ascii=False)
        if not calls and view['next_step'] and not final_only:
            self.store.update(run_id,text=self.failure_reply(state))
            feedback={'role':'system','content':'当前目标还有未执行的必要步骤：'+
                json.dumps(view['next_step'],ensure_ascii=False)+
                '。通过工具调用完成该步骤，收到实际结果后再回答；此消息没有执行工具。'}
            return {'messages':state['messages']+[feedback],'rounds':state['rounds']+1,
                    'calls':[],'research_plan':[],'deferred_results':[],
                    'continue_model':True,'final_only':final_only}
        if (not calls and not quote_selected and not final_only
                and state.get('route',{}).get('page_lookup',{}).get('target_kind')=='evidence_quote'):
            return {'messages':state['messages']+[{'role':'system','content':
                '用户请求原文，当前尚未读取可引用的目标正文。根据用户指定对象选择正文或简介读取工具，取得实际原文后回答。'}],
                'rounds':state['rounds']+1,'calls':[],'research_plan':[],'deferred_results':[],
                'continue_model':True,'final_only':final_only}
        sources = self.answer_input(state,[],True).sources
        if (not calls and not quote_selected and state.get('research')
                and state.get('route',{}).get('page_lookup',{}).get('target_kind')=='evidence_quote'
                and sources):
            message=await self.quote_reply(state,available)
            calls=validated_calls(message,state.get('extended_episodes',False))
        if (not calls and state.get('research')
                and (sources or not ordinary_question(state.get('route', {})))
                and not valid_citations(message.get('content'), sources)):
            # Citation numbers belong to one answer. A model may accidentally
            # reuse a previous turn's number even when all tool work succeeded.
            # Allow one text-only correction, never another execution attempt.
            repair = self.context.repair(self.answer_input(state, [], True), messages, message.get('content'))
            self.store.update(run_id, text=self.saved_reply(state))
            async def publish_repair(text):
                pass  # A repaired source reference is published after the bounded check.
            message = await self.invoke_model(run_id, 'citation_repair', self.settings, repair, [], publish_repair)
            if message.get('tool_calls') or not valid_citations(message.get('content'), sources):
                raise ModelFailure('invalid_citation')
        if not calls and plan:
            previous_progress=progress
            if direct and direct.get('goal_id'):
                progress=task_goals.delivered_query(state.get('route',{}),progress,direct['goal_id'],direct.get('proof_calls',[]))
            updated=task_goals.delivered_text(state.get('route',{}),progress,state['messages'])
            pending=[g for g in plan if task_goals.ready(g,previous_progress)
                     and task_goals.information_evidence_required(g,state.get('route',{}))
                     and updated.get(g['id'],{}).get('status') not in task_goals.TERMINAL]
            if pending and not final_only:
                self.store.update(run_id,text=self.failure_reply(state))
                return {'messages':state['messages']+[{'role':'system','content':
                    '以下查询尚未取得回答所需的真实工具结果：'+json.dumps(pending,ensure_ascii=False)+
                    '。继续选择并调用工具，收到实际结果后再回答。'}],
                    'rounds':state['rounds']+1,'calls':[],'research_plan':[],'deferred_results':[],
                    'continue_model':True,'final_only':final_only}
            self.store.update(run_id,goal_progress=updated)
            self.store.update(run_id,reported_controls=[r['call_id'] for r in receipt_evidence(last_turn(state['messages']))
                if r['tool'] in TOOLS and TOOLS[r['tool']].changes_state])
            done=[g['id'] for g in plan if previous_progress.get(g['id'],{}).get('status') not in task_goals.TERMINAL
                  and updated.get(g['id'],{}).get('status') in task_goals.TERMINAL]
            if done and not task_goals.complete(state.get('route',{}),updated):
                self.save_reply(state,message.get('content') or '',done)
                return await self.model_node({**state,'rounds':state['rounds']+1})
            progress=updated
        if not calls:
            self.store.update(run_id,reported_controls=[r['call_id'] for r in receipt_evidence(last_turn(state['messages']))
                if r['tool'] in TOOLS and TOOLS[r['tool']].changes_state])
            message={**message,'content':self.saved_reply(state,message.get('content') or '')}
        route = state.get('route', {})
        route_rechecked = state.get('route_rechecked', False)
        disputed = {c['name'] for c in calls if c['name'] in DEVICE_TOOLS and c['name'] not in allowed}
        if state.get('research') and disputed and not route_rechecked and not task_goals.goals(route):
            # A route is a lossy model classification, not evidence of authorization.
            # Recheck once before any effect; never open controls on a failed recheck.
            route_rechecked = True
            self.store.update(run_id, phase='intent')
            async def route_model(settings, messages, tools, publish):
                return await self.invoke_model(run_id, 'intent_recheck', settings, messages, tools, publish)
            try:
                checked = await classify(route_model, self.settings, state['messages'], [], disputed, conversation=self.conversation_input(state))
                route = {**route, 'device': route.get('device', False) or checked['device'],
                         'control_actions': checked['control_actions']}
                allowed = allowed_tools(route)
                _, allowed = recovery_scope(self.get_run(run_id), route, [], allowed)
                _, allowed = evidence_scope(state['messages'], [], allowed,web_delivery(route))
                self.store.update(run_id, route=route)
            except ModelFailure as exc:
                if exc.code == 'model_attempt_budget_exhausted':
                    raise
        message, calls, invalid_identities = prepare_calls(state['messages'], state.get('memory', {}),
            calls, message, allowed, run_id, state['rounds'])
        count = state.get('tool_count', 0)
        if count + len(calls) > MAX_TOOL_CALLS:
            # Do not partially execute an over-budget batch. Close every call
            # with a truthful receipt and reserve a tool-free final response.
            skipped = [{'role': 'tool', 'tool_call_id': c['id'], 'content': json.dumps(
                {'status': 'error', 'executed': False, 'reason': 'tool_budget_exhausted'})} for c in calls]
            return {'messages': state['messages'] + [message] + skipped,
                    'rounds': state['rounds'] + 1, 'calls': [], 'final_only': True, 'tool_count': count,'continue_model':False}
        deferred = []
        executable = []
        research_plan = []
        identity_rejections = 0
        remaining = remaining_controls(self.get_run(run_id), route) if 'control_actions' in route else None
        for call in calls:
            if call['name'] not in allowed:
                deferred.append(self.record_result(run_id, {'call_id': call['id'], 'result': {
                    'status': 'error', 'executed': False, 'code': 'tool_not_allowed', 'note': error_message('tool_not_allowed')}}))
                continue
            if remaining is not None and TOOLS[call['name']].changes_state:
                if remaining[call['name']] <= 0:
                    deferred.append(self.record_result(run_id, {'call_id': call['id'], 'result': {
                        'status': 'error', 'executed': False, 'code': 'control_not_requested',
                        'note': '本次请求没有尚待执行的这项操作。'}}))
                    continue
                remaining[call['name']] -= 1
            if call['id'] in invalid_identities:
                identity_rejections += invalid_identities[call['id']].startswith('unverified_')
                # No phone/research request has been sent for this call. Pair the
                # rejected plan with feedback so normal search can establish IDs.
                deferred.append(self.record_result(run_id, {'call_id': call['id'], 'result': {
                    'status': 'error', 'executed': False, 'code': invalid_identities[call['id']],
                    'note': ({'playback_identity_refresh_required':'本调用未执行。先调用get_playback_state取得完整节目身份，再选择工具继续原任务。',
                                  'rss_refresh_required':'本调用未执行。先调用list_episodes刷新该节目的本轮RSS；按原展示顺序保留用户选择，再决定播放调用。'}
                                 .get(invalid_identities[call['id']], '身份尚未核实，本调用未执行。先用search_catalog核对节目，再用list_episodes或search_rss取得真实分集ID；同批查询不能为本批其他调用提供身份。不得猜测ID或重放控制。'))}}))
                continue
            if call['name'] in SERVER_TOOLS:
                research_plan.append(call)
                continue
            selection = episode_selection_question(state['messages'], [call])
            if selection:
                deferred.append({'call_id': call['id'], 'result': {
                    'status': 'confirmation_required', 'executed': False,
                    'candidate': {**call['arguments'], **selection}}})
            else:
                executable.append(call)
        messages = state['messages'] + [message]
        if task_goals.goals(route):
            current=self.get_run(run_id)
            bindings=task_goals.bind(route,current.get('goal_progress',{}),current.get('goal_bindings',{}),executable+research_plan,state['messages'])
            self.store.update(run_id,goal_bindings=bindings)
        if not executable and not research_plan:
            messages += [{'role': 'tool', 'tool_call_id': r['call_id'],
                          'content': json.dumps(r['result'], ensure_ascii=False)} for r in deferred]
        memory = remember(state.get('memory', {}), run_id, calls, deferred) if deferred else state.get('memory', {})
        identity_repairs = state.get('identity_repairs', 0) + bool(identity_rejections)
        return {'messages': messages, 'rounds': state['rounds'] + 1,
                'calls': executable, 'research_plan': research_plan,
                'deferred_results': deferred if executable or research_plan else [],
                'tool_count': count + len(calls), 'memory': memory, 'route': route,
                'route_rechecked': route_rechecked, 'identity_repairs': identity_repairs,
                'final_only': final_only or identity_repairs >= 2,'continue_model':False}


    async def research_node(self, state):
        """Plan has committed with sync durability before any external request."""
        run_id = state['run_id']
        async def relevance_model(messages):
            async def discard(event): pass
            return await self.invoke_model(run_id, 'rss_relevance', replace(self.settings, agent_thinking=False, model_temperature=0), messages, [], discard)
        async def web_relevance_model(messages):
            async def discard(event): pass
            return await self.invoke_model(run_id, 'web_relevance', replace(self.settings, agent_thinking=False, model_temperature=0), messages, [], discard)
        plan = state.get('research_plan', [])
        results = list(state.get('deferred_results', []))
        for call in plan:
            run = self.get_run(run_id)
            ledger = {c['id']: c for c in run.get('tool_ledger', [])}
            if call['id'] in ledger and ledger[call['id']] != call:
                raise ModelFailure('invalid_tool_call')
            ledger[call['id']] = call
            self.store.update(run_id, tool_ledger=list(ledger.values()), phase=call['name'])
            old = run.get('raw_results', {}).get(call['id'])
            if old is None:
                old = next((r['result'] for r in run['results'] if r['call_id'] == call['id']), None)
            if old is None:
                started = list(run.get('research_started', []))
                if call['id'] in started:
                    old = {'status': 'unknown', 'code': 'research_result_unknown',
                           'note': error_message('research_result_unknown')}
                else:
                    # Conservative execution marker: unknown requests are never replayed.
                    self.store.update(run_id, research_started=started + [call['id']])
                    current = self.get_run(run_id)
                    goal=task_goals.call_goal(state.get('route',{}),current.get('goal_bindings',{}),call['id'])
                    as_of=datetime.now(timezone(timedelta(hours=8))).date().isoformat() if goal.get('as_of')=='current' else ''
                    old = await self.research.perform(call, ResearchContext(
                        run_id + ':' + call['id'], current.get('input', {}).get('message', ''),
                        tuple(current.get('sources', [])), relevance_model,as_of,known_urls(state['messages']),web_relevance_model,goal.get('request','')))
            results.append(self.record_result(run_id, {'call_id': call['id'], 'result': old}, call))
        messages = state['messages']
        if not state['calls']:
            ordered = {r['call_id']: r for r in results}
            results = [ordered[c['id']] for c in messages[-1]['tool_calls']]
            messages = messages + [{'role': 'tool', 'tool_call_id': r['call_id'],
                         'content': json.dumps(r['result'], ensure_ascii=False)} for r in results]
        memory = remember(state.get('memory', {}), run_id, plan, results)
        return {'messages': messages, 'research_plan': [], 'memory': memory,
                'deferred_results': results if state['calls'] else []}


    async def device_node(self, state, config: RunnableConfig):
        # No side effects before interrupt: this node restarts on resume.
        # Keep receipt/source persistence on the event-loop thread that owns
        # Store's SQLite connection; synchronous graph nodes run in a worker.
        # Python 3.10 needs the explicit Runnable context for interrupt/resume.
        with set_config_context(config) as context:
            results = context.run(interrupt, {'calls': state['calls']})
        self.store.update(state['run_id'], phase='device_receipt')
        results = results + state.get('deferred_results', [])
        calls = {c['id']: c for c in state['calls']}
        results = [self.record_result(state['run_id'], r, calls.get(r['call_id'])) for r in results]
        memory = state.get('memory')
        if memory is None:
            memory = replay_references({'turn': 1, 'groups': []}, state['run_id'], state['messages'])
        memory = remember(memory, state['run_id'], state['calls'], results)
        if state.get('research_plan'):
            # A model batch contains independent, already validated arguments.
            # Phone receipts commit first; a slow remote lookup cannot hold an
            # independent pause. Keep the assistant batch open until all tools
            # have receipts. Old checkpoints with completed research take the
            # original path below and retain their existing receipt identity.
            return {'calls': [], 'memory': memory, 'deferred_results': results}
        order = [c['id'] for c in state['messages'][-1]['tool_calls']]
        by_id = {r['call_id']: r for r in results}
        results = [by_id[i] for i in order]
        messages = state['messages'] + [{'role': 'tool', 'tool_call_id': r['call_id'],
                                       'content': json.dumps(r['result'], ensure_ascii=False)} for r in results]
        return {'messages': messages, 'calls': [], 'memory': memory, 'deferred_results': []}
