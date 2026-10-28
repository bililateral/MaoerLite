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
from maoer_agent.tools.results import checked_result, outcome_facts, receipt_evidence
from maoer_agent.context.assembler import AnswerInput, ContextAssembler, factual_delivery_needed, conversation_proofs
from maoer_agent.contracts.answers import FactualAnswer, AnswerReview, DraftReview, EvidencePlan, quotation_evidence
from maoer_agent.contracts.model import JsonMessages, model_call_context
from maoer_agent.contracts.tasks import checked_reply
from maoer_agent.contracts.execution import ResearchContext, PageView
from maoer_agent.core.limits import MAX_MODEL_ATTEMPTS
from maoer_agent.workflows import episode_pages
from maoer_agent.workflows.recommendations import criteria_call, duration_candidates
from maoer_agent.workflows import literal_listings
from maoer_agent.workflows.recovery import recovery_scope, recovered_selection_reply, remaining_controls
from maoer_agent.workflows.query_replies import reply_step, library_reply_step, library_completion_step, library_completion_call, waiting_selection_step, playback_state_step, unsupported_setting_step, control_receipt_step, selection_clarification_step
from maoer_agent.workflows.web import evidence_step, evidence_scope, lookup_step, known_urls
from maoer_agent.research.evidence import answer_sources
from maoer_agent.workflows import goals as task_goals


class ContinueGoalPlan(Exception):
    """An internal goal completed; dependent work still needs planning."""


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
        if purpose in {'intent','recovery_intent','answer_delivery','evidence_review','evidence_plan'}:
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
                                    ('model' if state['messages'][-1]['role'] == 'tool' else END))
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
            route = await classify(route_model, self.settings, state['messages'], self.catalog if 'search_hints' in self.get_run(run_id)['input'].get('capabilities', []) else [])
            extra['route'] = route
            self.store.update(run_id, search_hint=route['hint'], route=route, sources=[])
        return {'summary': saved['data'], 'summary_pending': len(pending), 'summary_candidates': [], **extra}


    def answer_input(self, state, available, final_only):
        progress=self.get_run(state['run_id']).get('goal_progress',{})
        route=task_goals.answer_route(state.get('route',{}),progress)
        return AnswerInput(state['messages'], state.get('memory', {}), state.get('summary', {}),
            state.get('summary_pending', 0), answer_sources(self.get_run(state['run_id']).get('sources', []), last_turn(state['messages']),web_delivery(state.get('route',{}))),
            {**route,'goal_progress':progress,'goal_view':task_goals.view(route,progress),
             'resolved_episode':resolved_episode(route,state['messages'])}, available, state.get('extended_episodes', False),
            state.get('research', False), final_only, self.settings.model,
            self.settings.context_budget_bytes, self.settings.agent_thinking)

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
            if (not (state.get('research') and (factual_delivery_needed(state.get('route', {})) or task_goals.goals(state.get('route',{}))))
                    and not state.get('route', {}).get('response_needs', {}).get('library_listing')):
                self.store.update(run_id, text=text, status='generating', retry_after=0)
        for attempt in range(3):
            self.store.update(run_id, text='', status='generating', retry_after=0, error='', error_detail={},
                              phase='model_analysis' if state.get('tool_count', 0) else 'model_planning')
            try:
                message = await self.invoke_model(run_id, 'main', self.settings,
                                           messages, [] if final_only else available, publish)
                if (not message.get('tool_calls') and not factual_delivery_needed(state.get('route',{}))
                        and not task_goals.goals(state.get('route',{}))):
                    checked_reply(message.get('content') or '',state.get('route',{}))
                break
            except ValueError as error:
                if attempt:raise ModelFailure('invalid_model_response') from None
                messages.insert(1,{'role':'system','content':'交付约束未满足：'+str(error)})
            except ModelFailure as exc:
                if (not exc.retryable or attempt == 2 or waited + exc.delay > 120
                        or time.monotonic() - started + exc.delay >= 180):
                    raise
                self.store.update(run_id, status='retry_wait', retry_after=exc.delay, error=exc.code,
                                  retry_at=int(time.time() + exc.delay))
                await asyncio.sleep(exc.delay)
                waited += exc.delay
        return message, messages

    async def factual_reply(self, state, available, correction=None):
        request = self.answer_input(state, [], True)
        feedback = correction if correction is not None else self.get_run(state['run_id']).get('answer_feedback')
        messages = self.context.factual_delivery(request, self.query_renderer, feedback)
        source_numbers = {source['number'] for source in request.sources}
        async def discard(text):
            pass  # Composition JSON and unverified text never enter the UI.
        for attempt in range(2):
            self.store.update(state['run_id'], phase='model_analysis', text='')
            try:
                reply = await self.invoke_model(state['run_id'], 'answer_delivery', self.settings, messages, [], discard)
            except ModelFailure as exc:
                if exc.code != 'incomplete_model_response' or exc.details.get('finish_reason') != 'length':
                    raise
                failures = list(self.get_run(state['run_id']).get('answer_delivery_failures', []))
                failures.append({'kind': exc.code, 'details': exc.details, 'purpose': 'answer_delivery'})
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
                delivery = FactualAnswer.model_validate_json(text)
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
        if task_goals.goals(state.get('route', {})) or any(s.get('kind') == 'web' and s.get('document_id') for s in request.sources):
            return await self.review_reply(state, message, available, blocks, revision_allowed=correction is None)
        return message

    async def review_reply(self, state, draft, available, draft_blocks=None, revision_allowed=True):
        readonly = task_goals.evidence_tools(state.get('route',{}),self.get_run(state['run_id']).get('goal_progress',{}),available)
        request = self.answer_input(state, readonly, not bool(readonly))
        run_id = state['run_id']
        # The no-source planning branch may propose plain text before composition.
        # Copy it into the same immutable block contract; never recover a stale draft.
        if draft_blocks is None:
            draft_blocks = [{'text': draft['content'], 'sources': []}] if draft.get('content') else []
        draft_blocks = [dict(b, sources=list(b.get('sources', []))) for b in draft_blocks]
        messages = self.context.evidence_review(request, self.query_renderer, draft_blocks, self.get_run(run_id).get('sources', []))
        names = {d['function']['name'] for d in readonly}
        proofs=self.context.review_proofs(request)
        previous_issues=[]
        receipts={row['call_id']:json.dumps(row,ensure_ascii=False)
                  for row in self.context.review_receipts(state['messages'],request.route.get('response_needs',{}).get('retrieval_statistics',False))}
        memory_proofs=conversation_proofs(request)
        receipts.update(memory_proofs)
        aliases={row['call_id']:[item for item in row['result'].get('items',[]) if item.get('evidence')
            and (item.get('kind')!='web' or item.get('document_id'))] for row in receipt_evidence(last_turn(state['messages']))}
        async def discard(text): pass
        for attempt in range(2):
            try:
                reply = await self.invoke_model(run_id, 'evidence_review', replace(self.settings,agent_thinking=False), messages, [], discard)
            except ModelFailure as exc:
                if attempt or exc.code != 'incomplete_model_response' or exc.details.get('finish_reason') != 'length':
                    raise
                failures = list(self.get_run(run_id).get('answer_delivery_failures', []))
                failures.append({'kind': exc.code, 'details': exc.details, 'purpose': 'evidence_review'})
                self.store.update(run_id, answer_delivery_failures=failures)
                messages.insert(1, {'role': 'system', 'content':
                    '上次结构化交付因达到输出长度上限而截断。保留全部用户目标及核对结果，'
                    '仅返回回答当前问题所需的结论、短引文和目标进度，避免扩展未请求的细节。'})
                continue
            try:
                if reply.get('tool_calls'):
                    raise ValueError('Review uses the explicit read-only decision contract')
                text = (reply.get('content') or '').strip()
                if text.startswith('```'):
                    text = text.split('\n', 1)[1].rsplit('```', 1)[0]
                decision = DraftReview.model_validate_json(text)
                if decision.decision=='ready' and previous_issues:
                    raise ValueError('The unchanged draft still has the previously evidenced defects; preserve their diagnosis for revision')
                blocks = decision.checked(draft_blocks, request.sources, names, receipts,aliases,
                    evidence_refs=proofs,episode=request.route.get('resolved_episode'))
                audits = list(self.get_run(run_id).get('draft_reviews', []))
                self.store.update(run_id, draft_reviews=audits + [{'draft': draft_blocks, **decision.model_dump()}])
                if decision.decision == 'revise':
                    if not revision_allowed:
                        raise ModelFailure('answer_review_failed', details={
                            'reason': 'revision_rejected',
                            'blocks': [c.block for c in decision.checks if c.issue != 'none']})
                    return await self.factual_reply(state, available,
                        correction=decision.revision_feedback(draft_blocks))
                if decision.decision == 'needs_evidence':
                    call = decision.lookup
                    call.goal_id=task_goals.lookup_goal(state.get('route',{}),self.get_run(run_id).get('goal_progress',{}),call)
                    message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                        'id': 'evidence_' + run_id + '_' + str(state.get('tool_count', 0)), 'type': 'function',
                        'function': {'name': call.name, 'arguments': json.dumps(call.arguments, ensure_ascii=False)}}]}
                    validated_calls(message, state.get('extended_episodes', False))
                else:
                    answer = self.query_renderer({'kind': 'factual_answer', 'blocks': blocks,
                        'sources': request.sources, 'link_sources': web_source_links(request.route), 'include_source_links': web_source_links(request.route)})
                    if not valid_citations(answer, request.sources):
                        raise ValueError('Review has invalid inline citations')
                    message = {'role': 'assistant', 'content': answer}
                reviews = list(self.get_run(run_id).get('evidence_reviews', []))
                progress=task_goals.reviewed(state.get('route',{}),self.get_run(run_id).get('goal_progress',{}),
                    decision.goals,request.sources,state['messages'],names,blocks,memory_proofs)
                quoted={q.number for check in decision.checks for q in check.evidence}
                missing_memory={a.id:sorted(set(a.memory_ids)-quoted) for a in decision.goals
                    if a.status=='satisfied' and set(a.memory_ids)-quoted}
                if missing_memory:
                    raise ValueError('Recalled statements need checked excerpts from their conversation evidence: '+json.dumps({
                        'missing_memory_ids_by_goal':missing_memory,
                        'reference_choices':{identity:[key for key,value in proofs.items() if value['number']==identity]
                            for identities in missing_memory.values() for identity in identities},
                        'goal_kinds':{g['id']:g['kind'] for g in request.route.get('task_plan',{}).get('goals',[])}},ensure_ascii=False))
                if decision.decision=='ready':
                    if any(p.get('status')=='waiting_user' for p in progress.values()) and not decision.question.strip():
                        raise ValueError('A waiting-user goal requires an explicit clarification question')
                    if decision.question and any(p.get('status')=='waiting_user' for p in progress.values()):
                        presentation=waiting_selection_step(request.route,progress,state['messages'],decision.question)
                        message={'role':'assistant','content':self.query_renderer(presentation or {'kind':'factual_answer','blocks':blocks,
                            'sources':request.sources,'question':decision.question,
                            'link_sources':web_source_links(request.route), 'include_source_links':web_source_links(request.route)})}
                        if not valid_citations(message['content'],request.sources):
                            raise ValueError('Clarification has invalid inline citations')
                # Preserve the established execution record shape; its delivered
                # blocks come from the stored draft, never from model review output.
                snapshot = {**decision.model_dump(), 'blocks': blocks or [], 'background': [],
                    'checks': [{'claim': draft_blocks[c.block]['text'][:600], 'issue': c.issue,
                        'evidence': [q.model_dump() for q in c.evidence]} for c in decision.checks]}
                feedback={}
                if decision.decision=='needs_evidence' and any(c.issue!='none' for c in decision.checks):
                    feedback={**decision.revision_feedback(draft_blocks),
                              'lookup_call_id':message['tool_calls'][0]['id']}
                if decision.decision=='needs_evidence' and decision.lookup.goal_id:
                    bindings=dict(self.get_run(run_id).get('goal_bindings',{}))
                    bindings[message['tool_calls'][0]['id']]=decision.lookup.goal_id
                    self.store.update(run_id,goal_bindings=bindings)
                self.store.update(run_id, evidence_reviews=reviews + [snapshot],goal_progress=progress,
                                  answer_feedback=feedback)
                if decision.decision=='ready' and not task_goals.complete(state.get('route',{}),progress):
                    updated,_=self.scoped_tools(state,False)
                    step=task_goals.initial_call(state.get('route',{}),progress,{d['function']['name'] for d in updated},state['messages'])
                    if step:
                        return {'role':'assistant','content':None,'tool_calls':[{'id':'goal_'+run_id+'_'+str(state.get('tool_count',0)),
                            'type':'function','function':{'name':step['name'],'arguments':json.dumps(step['arguments'])}}]}
                    raise ContinueGoalPlan()
                return message
            except (ValueError, KeyError, TypeError) as exc:
                failures = list(self.get_run(run_id).get('answer_delivery_failures', []))
                failures.append({'kind': type(exc).__name__, 'reason': str(exc),
                                 'content': reply.get('content') or '', 'purpose': 'evidence_review'})
                self.store.update(run_id, answer_delivery_failures=failures)
                if attempt:
                    raise ModelFailure('invalid_model_response') from None
                # A format error (for example revise plus lookup) does not erase
                # an independently valid diagnosis of this unchanged draft.
                try:
                    candidate=DraftReview.model_validate_json((reply.get('content') or '').strip())
                    defects=[c.model_dump() for c in candidate.checks if c.issue!='none']
                    if defects:
                        diagnosed=DraftReview.model_validate({'decision':'revise','checks':defects})
                        diagnosed.checked(draft_blocks,request.sources,names,receipts,aliases,evidence_refs=proofs)
                        previous_issues=diagnosed.revision_feedback(draft_blocks)['issues']
                except (ValueError,TypeError,KeyError):pass
                messages.insert(1, {'role': 'system', 'content':
                    '上次校核输出不符合结构或原文引用契约；按约定返回，quote须为对应已读原文中的连续文字，补查仅用可申请工具。'
                    +json.dumps({'contract_error':[
                        {'path':list(error['loc']),'type':error['type']}
                        for error in exc.errors(include_url=False,include_input=False)]
                        if hasattr(exc,'errors') else str(exc),
                        'previous_output':(reply.get('content') or '')[:10000],
                        'preserved_issues':previous_issues},ensure_ascii=False)})

    async def plan_evidence(self, state, available):
        readonly=task_goals.evidence_tools(state.get('route',{}),self.get_run(state['run_id']).get('goal_progress',{}),available)
        if not readonly:
            return await self.factual_reply(state,available)
        run_id=state['run_id']
        request=self.answer_input(state,readonly,False)
        messages=self.context.evidence_plan(request,self.get_run(run_id).get('sources',[]))
        names={d['function']['name'] for d in readonly}
        async def discard(_):pass
        for attempt in range(2):
            reply=await self.invoke_model(run_id,'evidence_plan',replace(self.settings,agent_thinking=False),messages,[],discard)
            try:
                if reply.get('tool_calls'):raise ValueError('Plan uses the explicit lookup contract')
                plan=EvidencePlan.model_validate_json((reply.get('content') or '').strip())
                if plan.lookup and plan.lookup.name not in names:raise ValueError('Lookup is outside the read-only scope')
                if plan.lookup:
                    plan.lookup.goal_id=task_goals.lookup_goal(state.get('route',{}),self.get_run(run_id).get('goal_progress',{}),plan.lookup)
                message=None
                if plan.lookup:
                    message={'role':'assistant','content':None,'tool_calls':[{
                        'id':'evidence_'+run_id+'_'+str(state.get('tool_count',0)),'type':'function',
                        'function':{'name':plan.lookup.name,'arguments':json.dumps(plan.lookup.arguments,ensure_ascii=False)}}]}
                    validated_calls(message,state.get('extended_episodes',False))
                    if plan.lookup.goal_id:
                        bindings=dict(self.get_run(run_id).get('goal_bindings',{}));bindings[message['tool_calls'][0]['id']]=plan.lookup.goal_id
                        self.store.update(run_id,goal_bindings=bindings)
                plans=list(self.get_run(run_id).get('evidence_plans',[]))
                self.store.update(run_id,evidence_plans=plans+[plan.model_dump()])
            except (ValueError,KeyError,TypeError) as exc:
                failures=list(self.get_run(run_id).get('evidence_plan_failures',[]))
                failures.append({'reason':str(exc),'content':reply.get('content') or ''})
                self.store.update(run_id,evidence_plan_failures=failures)
                if attempt:raise ModelFailure('invalid_model_response') from None
                messages.insert(1,{'role':'system','content':'上次查证计划不符合契约：'+str(exc)})
                continue
            return message if message else await self.factual_reply(state,available)


    async def page_message(self, state, names, final_only):
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
                return {'role': 'assistant', 'content': self.page_renderer(step)}
            call_id = 'page_' + run_id + '_' + str(state.get('tool_count', 0))
            return {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': call_id, 'type': 'function', 'function': {
                    'name': step['name'], 'arguments': json.dumps(step['arguments'], ensure_ascii=False)}}]}


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
            checked = await classify(recovery_intent, self.settings, state['messages'], [])
            route = {**state.get('route', {}), 'control_actions': checked['control_actions']}
            state = {**state, 'route': route}
            self.store.update(run_id, route=route)
        run=self.get_run(run_id)
        progress=task_goals.reconcile(state.get('route',{}),run.get('goal_progress',{}),run.get('goal_bindings',{}),state['messages'],run.get('control_blocked',False))
        if progress!=run.get('goal_progress',{}):self.store.update(run_id,goal_progress=progress)
        if task_goals.goals(state.get('route',{})) and task_goals.complete(state.get('route',{}),progress):final_only=True
        available,allowed=self.scoped_tools(state,final_only)
        closed=task_goals.close_failed_queries(state.get('route',{}),progress,state['messages'],allowed,run.get('sources',[]))
        if closed!=progress:
            progress=task_goals.reconcile(state.get('route',{}),closed,run.get('goal_bindings',{}),state['messages'],run.get('control_blocked',False))
            self.store.update(run_id,goal_progress=progress)
            if task_goals.complete(state.get('route',{}),progress):final_only=True
            available,allowed=self.scoped_tools(state,final_only)
        names = {d['function']['name'] for d in available}
        goal_call=None if final_only else task_goals.initial_call(state.get('route',{}),progress,names,state['messages'])
        if not final_only and not goal_call and 'query_library' in names:
            goal_call=library_completion_call(state.get('route',{}),state['messages'])
        if not final_only and not goal_call:
            goal_call=literal_listings.next_call(state.get('route',{}),state['messages'],names)
        plan=task_goals.goals(state.get('route',{}))
        observed_public=any(g['kind']=='public_query' and progress.get(g['id'],{}).get('status') in ('observed','needs_evidence') for g in plan)
        other_query=any(g['kind'] in ('library_query','catalog_query') and progress.get(g['id'],{}).get('status') not in task_goals.TERMINAL and task_goals.ready(g,progress) for g in plan)
        criteria = None if final_only else criteria_call(state.get('route', {}), state['messages'], names)
        selected = selection_step(state.get('route', {}), state['messages'], names) if state.get('extended_episodes') else None
        direct = (literal_listings.completion(state.get('route',{}),state['messages'])
                  or library_completion_step(state.get('route',{}),state['messages'])
                  or duration_candidates(state.get('route',{}),state['messages'])
                  or selection_clarification_step(state.get('route',{}),progress,state['messages'])
                  or control_receipt_step(state.get('route',{}),state['messages'])
                  or unsupported_setting_step(state.get('route',{}),progress)
                  or recovered_selection_reply(state.get('route', {}), state['messages'])
                  or (selected if selected and selected['kind']=='selection' and len(plan)<=1 else None)
                  or reply_step(state.get('route', {}), state['messages'], names)
                  or task_goals.failure_step(state.get('route',{}),progress,state['messages']))
        if direct and direct['kind']=='library':
            receipts={row['call_id']:json.dumps(row,ensure_ascii=False) for row in self.context.review_receipts(state['messages'])}
            decision=AnswerReview.model_validate({'decision':'ready','checks':direct['receipt_checks'],
                'blocks':[{'text':self.query_renderer(direct),'sources':[]}],
                'goals':[{'id':direct['goal_id'],'status':'satisfied','call_ids':direct['proof_calls']}]})
            blocks=decision.checked([],names,receipts)
            progress=task_goals.reviewed(state.get('route',{}),progress,decision.goals,[],state['messages'],names,blocks)
            self.store.update(run_id,goal_progress=progress,evidence_reviews=self.get_run(run_id).get('evidence_reviews',[])+[decision.model_dump()])
        if direct and direct['kind']=='duration_candidates':
            sources=answer_sources(self.get_run(run_id).get('sources',[]),last_turn(state['messages']))
            checks=[{'claim':item['title'],'issue':'none','evidence':[
                {'number':item['citation'],'quote':item['title']},
                {'number':item['citation'],'quote':json.dumps({'duration_seconds':item['duration_seconds']})}]} for item in direct['items']]
            decision=AnswerReview.model_validate({'decision':'ready','checks':checks,
                'blocks':[{'text':self.query_renderer(direct),'sources':[i['citation'] for i in direct['items']]}],
                'goals':[{'id':direct['goal_id'],'status':'satisfied','call_ids':direct['proof_calls'],
                          'sources':[i['citation'] for i in direct['items']]}]})
            blocks=decision.checked(sources,names)
            progress=task_goals.reviewed(state.get('route',{}),progress,decision.goals,sources,state['messages'],names,blocks)
            self.store.update(run_id,goal_progress=progress,evidence_reviews=self.get_run(run_id).get('evidence_reviews',[])+[decision.model_dump()])
        if direct and direct['kind']=='literal_titles':
            sources=answer_sources(self.get_run(run_id).get('sources',[]),last_turn(state['messages']))
            receipts={row['call_id']:json.dumps(row,ensure_ascii=False) for row in receipt_evidence(last_turn(state['messages']))}
            numbers=list(dict.fromkeys(item['citation'] for item in direct['items']))
            decision=AnswerReview.model_validate({'decision':'ready','checks':direct['checks'],
                'blocks':[{'text':self.query_renderer(direct),'sources':numbers}],
                'goals':[{'id':direct['goal_id'],'status':'satisfied','call_ids':direct['proof_calls'],'sources':numbers}]})
            blocks=decision.checked(sources,names,receipts)
            progress=task_goals.reviewed(state.get('route',{}),progress,decision.goals,sources,state['messages'],names,blocks)
            self.store.update(run_id,goal_progress=progress,evidence_reviews=self.get_run(run_id).get('evidence_reviews',[])+[decision.model_dump()])
        canonical = (direct and direct['kind'] in {'query_failures','literal_titles','library','duration_candidates','selection_clarification','acknowledgement','control_receipts','unsupported_setting','unsupported','cancel_timer_explanation','selection','episode_page','recovered_selection'}
                     and (not plan or task_goals.complete(state.get('route',{}),progress)))
        if self.get_run(run_id).get('playback_refresh_pending') and not final_only:
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'recovery_state_' + run_id + '_' + str(state.get('tool_count', 0)),
                'type': 'function', 'function': {'name': 'get_playback_state', 'arguments': '{}'}}]}
            messages = state['messages']
        elif goal_call:
            message={'role':'assistant','content':None,'tool_calls':[{'id':'goal_'+run_id+'_'+str(state.get('tool_count',0)),
                'type':'function','function':{'name':goal_call['name'],'arguments':json.dumps(goal_call['arguments'])}}]}
            messages=state['messages']
        elif canonical:
            message = {'role': 'assistant', 'content': self.query_renderer(direct)}
            messages = state['messages']
        elif plan and (task_goals.complete(state.get('route',{}),progress) or (observed_public and not other_query)):
            previous=self.get_run(run_id).get('evidence_reviews',[])
            blocks=previous[-1].get('blocks',[]) if previous else []
            message={'role':'assistant','content':self.query_renderer({'kind':'factual_answer','blocks':blocks}) if blocks else ''}
            messages=state['messages']
        elif direct:
            message = {'role': 'assistant', 'content': self.query_renderer(direct)}
            messages = state['messages']
        elif criteria:
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'criteria_' + run_id + '_' + str(state.get('tool_count', 0)), 'type': 'function',
                'function': {'name': criteria['name'], 'arguments': json.dumps(criteria['arguments'], ensure_ascii=False)}}]}
            messages = state['messages']
        elif selected:
            if selected['kind'] == 'selection':
                message = {'role': 'assistant', 'content': self.query_renderer(selected)}
            else:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'selection_' + run_id + '_' + str(state.get('tool_count', 0)), 'type': 'function',
                    'function': {'name': selected['name'], 'arguments': json.dumps(selected['arguments'], ensure_ascii=False)}}]}
            messages = state['messages']
        elif state.get('research') and episode_pages.active(state.get('route', {})):
            message = await self.page_message(state, names, final_only)
            canonical=not bool(message.get('tool_calls'))
            messages = state['messages']
        else:
            message, messages = await self.answer_message(state, available, final_only)
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
        if not calls and not canonical and not final_only and state.get('research'):
            # A proposed final answer can omit a requested observation. Fill only
            # that missing prerequisite, then return to normal model planning.
            # Existing receipts are not a cache or a cap on new model-selected
            # reads: those keep their own call identity and normal budget.
            missing = task_goals.missing_query(state.get('route',{}),progress,names) if plan else (
                playback_state_step(state.get('route', {}), state['messages'], names)
                or lookup_step(state.get('route', {}), state['messages'], names)
                or evidence_step(state['messages'], names,web_delivery(state.get('route',{}))))
            if missing:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'missing_query_' + run_id + '_' + str(state.get('tool_count', 0)),
                    'type': 'function', 'function': {
                        'name': missing['name'], 'arguments': json.dumps(missing['arguments'], ensure_ascii=False)}}]}
                calls = validated_calls(message, state.get('extended_episodes', False))
        if not calls and not canonical:
            library = library_reply_step(state.get('route', {}), state['messages'])
            if library:
                message = {'role': 'assistant', 'content': self.query_renderer(library)}
        sources = answer_sources(self.get_run(run_id).get('sources', []), last_turn(state['messages']),web_delivery(state.get('route',{})))
        if not calls and not canonical and state.get('research') and (task_goals.goals(state.get('route',{})) or factual_delivery_needed(state.get('route', {}))):
            try:
                if task_goals.goals(state.get('route',{})) and not sources and not task_goals.complete(state.get('route',{}),progress):
                    message=await self.plan_evidence(state,available)
                else:message = await self.factual_reply(state, available)
            except ContinueGoalPlan:
                return await self.model_node({**state,'rounds':state['rounds']+1})
            available,allowed=self.scoped_tools(state,final_only)
            calls = validated_calls(message, state.get('extended_episodes', False))
        if not calls and state.get('research') and not valid_citations(message.get('content'), sources):
            # Citation numbers belong to one answer. A model may accidentally
            # reuse a previous turn's number even when all tool work succeeded.
            # Allow one text-only correction, never another execution attempt.
            repair = self.context.repair(self.answer_input(state, [], True), messages, message.get('content'))
            self.store.update(run_id, text='')
            async def publish_repair(text):
                self.store.update(run_id, text=text, status='generating', retry_after=0)
            message = await self.invoke_model(run_id, 'citation_repair', self.settings, repair, [], publish_repair)
            if message.get('tool_calls') or not valid_citations(message.get('content'), sources):
                raise ModelFailure('invalid_citation')
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
                checked = await classify(route_model, self.settings, state['messages'], [], disputed)
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
                    'rounds': state['rounds'] + 1, 'calls': [], 'final_only': True, 'tool_count': count}
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
                identity_rejections += 1
                # No phone/research request has been sent for this call. Pair the
                # rejected plan with feedback so normal search can establish IDs.
                deferred.append(self.record_result(run_id, {'call_id': call['id'], 'result': {
                    'status': 'error', 'executed': False, 'code': invalid_identities[call['id']],
                    'note': '身份尚未核实，本调用未执行。先用search_catalog核对节目，再用list_episodes或search_rss取得真实分集ID；同批查询不能为本批其他调用提供身份。不得猜测ID或重放控制。'}}))
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
        if not calls and (not direct or direct['kind']!='query_failures'):
            failures=task_goals.query_failures(route,self.get_run(run_id).get('goal_progress',{}))
            if failures:
                message={**message,'content':'\n\n'.join(filter(None,[message.get('content'),self.query_renderer({'kind':'query_failures','failures':failures})]))}
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
                'final_only': final_only or identity_repairs >= 2}


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
