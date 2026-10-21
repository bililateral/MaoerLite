"""LangGraph nodes; the durable device boundary and checkpoint names are unchanged."""
import asyncio
import json
import time
from dataclasses import replace
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import set_config_context
from maoer_agent.contracts.errors import ModelFailure
from maoer_agent.core.errors import error_message
from maoer_agent.tools.definitions import validated_calls
from maoer_agent.memory.history import last_turn, remember, replay_references
from maoer_agent.memory.summary import MAX_BATCHES, SUMMARY_TIMEOUT, batch, parse_summary, summary_messages
from maoer_agent.workflows.selection import episode_selection_question, selection_step
from maoer_agent.workflows.planning import prepare_calls, tool_scope
from maoer_agent.agent.state import State, MAX_TOOL_CALLS, MAX_MODEL_ROUNDS
from maoer_agent.workflows.intent import classify, allowed_tools, DEVICE_TOOLS
from maoer_agent.tools.definitions import SERVER_TOOLS
from maoer_agent.research.sources import annotate, valid_citations
from maoer_agent.tools.results import checked_result, outcome_facts
from maoer_agent.context.assembler import AnswerInput, ContextAssembler
from maoer_agent.contracts.execution import ResearchContext, PageView
from maoer_agent.core.limits import MAX_MODEL_ATTEMPTS
from maoer_agent.workflows import episode_pages
from maoer_agent.workflows.recommendations import criteria_call
from maoer_agent.workflows.recovery import recovery_scope


class AgentGraph:
    def __init__(self, settings, store, model, get_run, research, catalog, page_renderer, selection_renderer):
        self.settings, self.store, self.model, self.get_run = settings, store, model, get_run
        self.research = research
        self.catalog = catalog
        self.page_renderer = page_renderer
        self.selection_renderer = selection_renderer
        self.context = ContextAssembler()
        self.feed_urls = frozenset(i['feed_url'] for i in catalog)

    async def invoke_model(self, run_id, purpose, settings, messages, tools, publish):
        run = self.get_run(run_id)
        usage = dict(run.get('model_attempts', {}))
        if sum(usage.values()) >= MAX_MODEL_ATTEMPTS:
            raise ModelFailure('model_attempt_budget_exhausted')
        usage[purpose] = usage.get(purpose, 0) + 1
        self.store.update(run_id, model_attempts=usage)
        return await self.model(settings, messages, tools, publish)

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
        graph.add_conditional_edges('model', lambda state: 'research' if state.get('research_plan') else 'device' if state['calls'] else
                                    ('model' if state['messages'][-1]['role'] == 'tool' else END))
        graph.add_conditional_edges('research', lambda state: 'device' if state['calls'] else 'model')
        graph.add_edge('device', 'model')
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
        async def discard(_):
            pass  # Summary text must never be streamed as the assistant's answer.
        for _ in range(MAX_BATCHES):
            if not pending:
                break
            self.store.update(run_id, status='summarizing', phase='summary', summary_turns=len(saved['covered']),
                              summary_pending=len(pending))
            ids, records = batch(pending)
            try:
                message = await asyncio.wait_for(self.invoke_model(run_id, 'summary', replace(self.settings, agent_thinking=False),
                    summary_messages(saved['data'], records), [], discard), SUMMARY_TIMEOUT)
                data = parse_summary(message)
            except (ModelFailure, ValueError, asyncio.TimeoutError) as exc:
                # Preserve the last good summary and its cursor. No recursive
                # retry or lost coverage: the next user task can try again.
                failure = exc.code if isinstance(exc, ModelFailure) else 'summary_unavailable'
                break
            saved = {'data': data, 'covered': saved['covered'] + ids}
            self.store.save_summary(conversation, saved)
            pending = pending[len(ids):]
        self.store.update(run_id, status='generating', summary_turns=len(saved['covered']),
                          summary_pending=len(pending), summary_error=failure)
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
        return AnswerInput(state['messages'], state.get('memory', {}), state.get('summary', {}),
            state.get('summary_pending', 0), self.get_run(state['run_id']).get('sources', []),
            state.get('route', {}), available, state.get('extended_episodes', False),
            state.get('research', False), final_only, self.settings.model,
            self.settings.context_budget_bytes, self.settings.agent_thinking)

    async def answer_message(self, state, available, final_only):
        run_id = state['run_id']
        request = self.answer_input(state, available, final_only)
        prepared = self.context.answer(request)
        messages, metrics = prepared.messages, prepared.metrics
        self.store.update(run_id, context_budget=metrics)
        started, waited = time.monotonic(), 0
        async def publish(text):
            self.store.update(run_id, text=text, status='generating', retry_after=0)
        for attempt in range(3):
            self.store.update(run_id, text='', status='generating', retry_after=0, error='', error_detail={},
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
                        episode_pages.match_messages(state['route']['page_lookup'], step['item']), [], discard)
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
        available, allowed = tool_scope(state.get('route', {}),
            self.get_run(run_id)['input'].get('capabilities', []),
            state.get('extended_episodes', False), state.get('research', False), final_only)
        available, allowed = recovery_scope(self.get_run(run_id), state.get('route', {}), available, allowed)
        names = {d['function']['name'] for d in available}
        criteria = None if final_only else criteria_call(state.get('route', {}), state['messages'], names)
        selected = selection_step(state.get('route', {}), state['messages'], names) if state.get('extended_episodes') else None
        if self.get_run(run_id).get('playback_refresh_pending') and not final_only:
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'recovery_state_' + run_id + '_' + str(state.get('tool_count', 0)),
                'type': 'function', 'function': {'name': 'get_playback_state', 'arguments': '{}'}}]}
            messages = state['messages']
        elif criteria:
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'criteria_' + run_id + '_' + str(state.get('tool_count', 0)), 'type': 'function',
                'function': {'name': criteria['name'], 'arguments': json.dumps(criteria['arguments'], ensure_ascii=False)}}]}
            messages = state['messages']
        elif selected:
            if selected['kind'] == 'selection':
                message = {'role': 'assistant', 'content': self.selection_renderer(selected)}
            else:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'selection_' + run_id + '_' + str(state.get('tool_count', 0)), 'type': 'function',
                    'function': {'name': selected['name'], 'arguments': json.dumps(selected['arguments'], ensure_ascii=False)}}]}
            messages = state['messages']
        elif state.get('research') and episode_pages.active(state.get('route', {})):
            message = await self.page_message(state, names, final_only)
            messages = state['messages']
        else:
            message, messages = await self.answer_message(state, available, final_only)
        try:
            calls = validated_calls(message, state.get('extended_episodes', False))
        except (ValueError, KeyError, TypeError):
            raise ModelFailure('invalid_tool_call') from None
        if final_only and calls:
            raise ModelFailure('tool_round_limit')
        if not calls and state.get('research') and not valid_citations(message.get('content'), self.get_run(run_id).get('sources', [])):
            # Citation numbers belong to one answer. A model may accidentally
            # reuse a previous turn's number even when all tool work succeeded.
            # Allow one text-only correction, never another execution attempt.
            repair = self.context.repair(self.answer_input(state, [], True), messages, message.get('content'))
            self.store.update(run_id, text='')
            async def publish_repair(text):
                self.store.update(run_id, text=text, status='generating', retry_after=0)
            message = await self.invoke_model(run_id, 'citation_repair', self.settings, repair, [], publish_repair)
            if message.get('tool_calls') or not valid_citations(message.get('content'), self.get_run(run_id).get('sources', [])):
                raise ModelFailure('invalid_citation')
        route = state.get('route', {})
        route_rechecked = state.get('route_rechecked', False)
        disputed = {c['name'] for c in calls if c['name'] in DEVICE_TOOLS and c['name'] not in allowed}
        if state.get('research') and disputed and not route_rechecked:
            # A route is a lossy model classification, not evidence of authorization.
            # Recheck once before any effect; never open controls on a failed recheck.
            route_rechecked = True
            self.store.update(run_id, phase='intent')
            async def route_model(settings, messages, tools, publish):
                return await self.invoke_model(run_id, 'intent_recheck', settings, messages, tools, publish)
            try:
                checked = await classify(route_model, self.settings, state['messages'], [], disputed)
                route = {**route, 'device': route.get('device', False) or checked['device']}
                allowed = allowed_tools(route)
                _, allowed = recovery_scope(self.get_run(run_id), route, [], allowed)
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
        for call in calls:
            if call['name'] not in allowed:
                deferred.append(self.record_result(run_id, {'call_id': call['id'], 'result': {
                    'status': 'error', 'executed': False, 'code': 'tool_not_allowed', 'note': error_message('tool_not_allowed')}}))
                continue
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
        messages = state['messages'] + [message]
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
                    old = await self.research.perform(call, ResearchContext(
                        run_id + ':' + call['id'], current.get('input', {}).get('message', ''),
                        tuple(current.get('sources', []))))
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
        order = [c['id'] for c in state['messages'][-1]['tool_calls']]
        by_id = {r['call_id']: r for r in results}
        results = [by_id[i] for i in order]
        messages = state['messages'] + [{'role': 'tool', 'tool_call_id': r['call_id'],
                                       'content': json.dumps(r['result'], ensure_ascii=False)} for r in results]
        memory = state.get('memory')
        if memory is None:
            memory = replay_references({'turn': 1, 'groups': []}, state['run_id'], state['messages'])
        memory = remember(memory, state['run_id'], state['calls'], results)
        return {'messages': messages, 'calls': [], 'memory': memory, 'deferred_results': []}
