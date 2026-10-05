"""LangGraph nodes; the durable device boundary and checkpoint names are unchanged."""
import asyncio
import json
import time
from dataclasses import replace
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import set_config_context
from agent_model import ModelFailure
from agent_tools import definitions, validated_calls
from agent_prompts import model_instructions
from agent_memory import last_turn, model_messages, references, remember, replay_references
from agent_summary import MAX_BATCHES, SUMMARY_TIMEOUT, batch, parse_summary, summary_messages, with_summary
from selection_policy import selection_identity_refresh, playback_preflight, episode_selection_question
from task_state import State, MAX_TOOL_CALLS, MAX_MODEL_ROUNDS


class AgentGraph:
    def __init__(self, settings, store, model, get_run):
        self.settings, self.store, self.model, self.get_run = settings, store, model, get_run

    def compile(self, saver):
        graph = StateGraph(State)
        graph.add_node('prepare', self.prepare_node)
        graph.add_node('model', self.model_node)
        graph.add_node('device', self.device_node)
        graph.add_edge(START, 'prepare')
        graph.add_edge('prepare', 'model')
        graph.add_conditional_edges('model', lambda state: 'device' if state['calls'] else
                                    ('model' if state['messages'][-1]['role'] == 'tool' else END))
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
            self.store.update(run_id, status='summarizing', summary_turns=len(saved['covered']),
                              summary_pending=len(pending))
            ids, records = batch(pending)
            try:
                message = await asyncio.wait_for(self.model(replace(self.settings, agent_thinking=False),
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
        return {'summary': saved['data'], 'summary_pending': len(pending), 'summary_candidates': []}


    async def model_node(self, state):
        if state['rounds'] >= MAX_MODEL_ROUNDS:
            raise ModelFailure('tool_round_limit')
        run_id = state['run_id']
        final_only = state.get('final_only', False) or state.get('tool_count', 0) >= MAX_TOOL_CALLS
        messages = model_messages(state['messages'], state.get('memory', {}))
        messages = with_summary(messages, state.get('summary', {}), state.get('summary_pending', 0) > 0)
        messages = model_instructions(messages, state['messages'], self.settings.model, final_only)
        started, waited = time.monotonic(), 0
        async def publish(text):
            self.store.update(run_id, text=text, status='generating', retry_after=0)
        for attempt in range(3):
            self.store.update(run_id, text='', status='generating', retry_after=0, error='')
            try:
                message = await self.model(self.settings,
                                           messages, [] if final_only else definitions(state.get('extended_episodes', False)), publish)
                break
            except ModelFailure as exc:
                if (not exc.retryable or attempt == 2 or waited + exc.delay > 120
                        or time.monotonic() - started + exc.delay >= 180):
                    raise
                self.store.update(run_id, status='retry_wait', retry_after=exc.delay, error=exc.code,
                                  retry_at=int(time.time() + exc.delay))
                await asyncio.sleep(exc.delay)
                waited += exc.delay
        calls = validated_calls(message, state.get('extended_episodes', False))
        if final_only and calls:
            raise ModelFailure('tool_round_limit')
        if calls:
            # No tool is dispatched until complete model output and validation.
            known = list(references(state.get('memory', {})))
            for previous in last_turn(state['messages']):
                if previous['role'] == 'tool':
                    result = json.loads(previous['content'])
                    if result.get('status') == 'ok':
                        known.extend(result.get('items', []))
            refresh_identity = selection_identity_refresh(state['messages'], calls, known, run_id)
            if refresh_identity:
                calls = [refresh_identity]
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': refresh_identity['id'], 'type': 'function',
                    'function': {'name': 'get_playback_state', 'arguments': '{}'}}]}
            for call in calls:
                args = call['arguments']
                if (call['name'] in ('list_episodes', 'get_podcast_details') or
                    (call['name'] == 'search_podcast_content' and args.get('podcast_id'))) and not any(
                        (item.get('id') == args['podcast_id'] and item.get('kind') == 'podcast') or
                        (item.get('podcast_id') == args['podcast_id'] and item.get('kind') == 'episode') for item in known):
                    raise ModelFailure('unverified_podcast_id')
                if call['name'] == 'play_episode' and not any(
                        item.get('id') == args['episode_id'] and item.get('podcast_id') == args['podcast_id']
                        and item.get('kind') == 'episode' for item in known):
                    raise ModelFailure('unverified_episode_id')
            ids = [c['id'] for c in calls]
            seen = {c['id'] for m in state['messages'] for c in m.get('tool_calls', [])}
            if any(not i or i in seen for i in ids):
                raise ModelFailure('invalid_tool_call')
            executable = [c for c in calls if not episode_selection_question(state['messages'], [c])]
            refresh = playback_preflight(state['messages'], executable, run_id, state['rounds'])
            if refresh:
                if any(call['id'] in seen for call in refresh):
                    raise ModelFailure('invalid_tool_call')
                calls = refresh
                message = {'role': 'assistant', 'content': '播放前先核对本轮RSS，再继续完成用户要求；原控制计划尚未执行。用户选择旧列表位置时仍按原展示列表，不用补查列表重新编号；用户要求最新时按刷新结果选择。',
                           'tool_calls': [{'id': c['id'], 'type': 'function', 'function': {
                               'name': c['name'], 'arguments': json.dumps(c['arguments'], ensure_ascii=False)}} for c in calls]}
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
        for call in calls:
            selection = episode_selection_question(state['messages'], [call])
            if selection:
                deferred.append({'call_id': call['id'], 'result': {
                    'status': 'confirmation_required', 'executed': False,
                    'candidate': {**call['arguments'], **selection}}})
            else:
                executable.append(call)
        messages = state['messages'] + [message]
        if not executable:
            messages += [{'role': 'tool', 'tool_call_id': r['call_id'],
                          'content': json.dumps(r['result'], ensure_ascii=False)} for r in deferred]
        return {'messages': messages, 'rounds': state['rounds'] + 1,
                'calls': executable, 'deferred_results': deferred if executable else [],
                'tool_count': count + len(calls)}


    def device_node(self, state, config: RunnableConfig):
        # No side effects before interrupt: this node restarts on resume.
        # Python 3.10 does not propagate Runnable context through async tasks.
        # Explicitly use the supplied public config/context for this sync node.
        with set_config_context(config) as context:
            results = context.run(interrupt, {'calls': state['calls']})
        results = results + state.get('deferred_results', [])
        messages = state['messages'] + [{'role': 'tool', 'tool_call_id': r['call_id'],
                                       'content': json.dumps(r['result'], ensure_ascii=False)} for r in results]
        memory = state.get('memory')
        if memory is None:
            memory = replay_references({'turn': 1, 'groups': []}, state['run_id'], state['messages'])
        memory = remember(memory, state['run_id'], state['calls'], results)
        return {'messages': messages, 'calls': [], 'memory': memory, 'deferred_results': []}
