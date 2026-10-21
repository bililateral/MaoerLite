"""Durable single-agent runs. Device operations happen only on Android."""
import asyncio
import logging
import time
import sqlite3
from langgraph.types import Command
from langsmith import tracing_context
from maoer_agent.contracts.errors import ModelFailure
from maoer_agent.context.prompts import SYSTEM, selection_policy
from maoer_agent.memory.history import context, last_turn
from maoer_agent.agent.graph import AgentGraph
from maoer_agent.tools.results import outcome_facts
from maoer_agent.agent.state import TaskConflict, TERMINAL
from maoer_agent.core.limits import GRAPH_RECURSION_LIMIT
from maoer_agent.runtime.dispatch import ToolDispatcher


class AgentTasks:
    def __init__(self, settings, saver, store, model, research, catalog, page_renderer, query_renderer):
        self.settings, self.store = settings, store
        self.saver = saver
        self.deletion_lock = asyncio.Lock()
        self.model = model
        self.jobs = {}
        self.dispatcher = ToolDispatcher(store, self.get_run)
        self.graph = AgentGraph(settings, store, model, self.get_run, research, catalog, page_renderer, query_renderer).compile(saver)

    def launch(self, run_id, value):
        if run_id in self.jobs: raise TaskConflict('task_busy')
        self.jobs[run_id] = asyncio.create_task(self.drive(run_id, value))

    def advance_batch(self, run_id, calls):
        return self.dispatcher.advance_batch(run_id, calls)

    async def drive(self, run_id, value):
        try:
            # Keep conversation data local even if the shell has tracing env vars.
            while True:
                with tracing_context(enabled=False):
                    result = await self.graph.ainvoke(value, {'configurable': {'thread_id': run_id}, 'recursion_limit': GRAPH_RECURSION_LIMIT}, durability='sync')
                if result.get('__interrupt__'):
                    results = self.advance_batch(run_id, result['calls'])
                    if results is None:
                        break
                    value = Command(resume=results)
                else:
                    self.store.update(run_id, status='completed', calls=[], text=result['messages'][-1].get('content') or '',
                                      history=last_turn(result['messages']), outcome=outcome_facts(last_turn(result['messages'])), memory=result.get('memory', {}), error='', error_detail={}, phase='', retry_after=0)
                    break
        except asyncio.CancelledError:
            # Explicit cancellation and service shutdown choose their status outside this task.
            raise
        except ModelFailure as exc:
            logging.getLogger('maoer.agent').warning('agent_run=%s phase=%s code=%s details=%s',
                run_id, self.get_run(run_id).get('phase', 'unknown'), exc.code, exc.details)
            self.store.update(run_id, status='failed', calls=[], error=exc.code, error_detail=exc.details, retry_after=exc.delay)
        except Exception as exc:
            logging.getLogger('maoer.agent').warning('agent_run=%s failure_type=%s', run_id, type(exc).__name__)
            self.store.update(run_id, status='failed', calls=[],
                              error='server_storage_error' if isinstance(exc, sqlite3.Error) else 'invalid_agent_response', error_detail={})
        finally:
            self.jobs.pop(run_id, None)

    def create(self, request):
        if self.store.deleted(request.conversation_id): raise TaskConflict('conversation_deleted')
        value = request.model_dump()
        # Preserve idempotency for requests persisted before capability negotiation existed.
        if not value.get('capabilities'):
            value.pop('capabilities', None)
        try:
            existing = self.store.get(request.id)
            if existing['input'] != value: raise TaskConflict('request_id_reused')
            return existing
        except KeyError:
            pass
        previous = self.store.conversation(request.conversation_id)
        if any(r['status'] not in TERMINAL for r in previous): raise TaskConflict('conversation_busy')
        history, memory, evicted = context(previous)
        extended = 'episode_selection_v2' in value.get('capabilities', [])
        messages = [{'role': 'system', 'content': SYSTEM + selection_policy(extended)}] + history + [{'role': 'user', 'content': request.message}]
        initial = {'run_id': request.id, 'messages': messages, 'rounds': 0, 'calls': [], 'memory': memory,
                   'tool_count': 0, 'final_only': False, 'summary_candidates': [r['id'] for r in evicted],
                   'summary': {}, 'summary_pending': 0, 'extended_episodes': extended,
                   'research': 'research_v3' in value.get('capabilities', [])}
        run = self.store.put({'id': request.id, 'conversation_id': request.conversation_id, 'status': 'generating',
            'text': '', 'calls': [], 'results': [], 'claimed': [], 'receipts': {}, 'error': '', 'retry_after': 0,
            'retry_at': 0, 'version': 0, 'input': value, 'history': [], 'memory': memory, 'created_at': int(time.time()),
            'summary_turns': 0, 'summary_pending': 0})
        self.launch(request.id, initial)
        return run

    def get_run(self, run_id):
        run = self.store.get(run_id)
        if self.store.deleted(run['conversation_id']): raise KeyError(run_id)
        return run

    def claim(self, run_id, call_id):
        return self.dispatcher.claim(run_id, call_id)

    def submit(self, run_id, results):
        result = self.dispatcher.submit(run_id, results, busy=run_id in self.jobs)
        if result.resume is not None:
            self.launch(run_id, Command(resume=result.resume))
        return result.run

    async def cancel(self, run_id):
        run = self.get_run(run_id)
        if run['status'] not in TERMINAL:
            job = self.jobs.get(run_id)
            if job:
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
            run = self.store.update(run_id, status='cancelled', calls=[], error='', retry_after=0)
        return run

    async def retry(self, run_id):
        run = self.get_run(run_id)
        if run['status'] != 'failed' or run_id in self.jobs: raise TaskConflict('task_not_retryable')
        if run['error'] in ('tool_round_limit', 'model_attempt_budget_exhausted', 'context_budget_exhausted'): raise TaskConflict('task_not_retryable')
        with tracing_context(enabled=False):
            snapshot = await self.graph.aget_state({'configurable': {'thread_id': run_id}})
        # Deletion may begin while the checkpointer read is awaited.
        try: run = self.get_run(run_id)
        except KeyError: raise TaskConflict('conversation_deleted') from None
        if run['status'] != 'failed' or run_id in self.jobs: raise TaskConflict('task_not_retryable')
        value = None
        interrupted = [task for task in snapshot.tasks if task.interrupts]
        if interrupted:
            # A node may fail after interrupt() consumed the device receipts.
            # Resume only a fully recorded batch; never dispatch/claim it again.
            calls = snapshot.values.get('calls', [])
            receipts = {r['call_id']: r for r in run.get('results', [])}
            if (any(task.name != 'device' or not task.error for task in interrupted)
                    or not calls or any(c['id'] not in receipts for c in calls)):
                raise TaskConflict('task_not_retryable')
            value = Command(resume=[receipts[c['id']] for c in calls])
        elif not snapshot.next:
            raise TaskConflict('task_not_retryable')
        run = self.store.update(run_id, status='generating', text='', error='', error_detail={}, retry_after=0)
        self.launch(run_id, value)
        return run

    async def delete_conversation(self, conversation):
        async with self.deletion_lock:
            runs = self.store.conversation(conversation)
            if any(r['status'] not in TERMINAL or r['id'] in self.jobs for r in runs):
                raise TaskConflict('conversation_busy')
            # Durable intent precedes awaits. New tasks/retries cannot resurrect it.
            self.store.mark_deleted(conversation)
            for run in runs:
                await self.saver.adelete_thread(run['id'])
            # Keep rows until every checkpoint deletion succeeds, allowing restart
            # or a repeated DELETE to finish a partially completed deletion.
            self.store.remove_conversation(conversation)

    async def recover(self):
        conversations = self.store.conversations()
        for conversation in conversations:
            if self.store.deleted(conversation):
                await self.delete_conversation(conversation)
        for run in self.store.runs():
            if run['status'] in TERMINAL: continue
            with tracing_context(enabled=False):
                snapshot = await self.graph.aget_state({'configurable': {'thread_id': run['id']}})
            messages = snapshot.values.get('messages', [])
            if (not snapshot.next and messages and messages[-1].get('role') == 'assistant'
                    and not messages[-1].get('tool_calls') and not snapshot.values.get('calls')):
                # The final checkpoint can commit before the public snapshot.
                # Recover its answer without another model call or device effect.
                self.store.update(run['id'], status='completed', calls=[],
                                  text=messages[-1].get('content') or '', history=last_turn(messages), outcome=outcome_facts(last_turn(messages)),
                                  memory=snapshot.values.get('memory', run.get('memory', {})), error='', retry_after=0)
            elif snapshot.tasks and any(task.interrupts for task in snapshot.tasks):
                calls = snapshot.values.get('calls', [])
                results = self.advance_batch(run['id'], calls)
                if results is not None:
                    self.launch(run['id'], Command(resume=results))
            else:
                # Never blindly replay an interrupted model or device side effect.
                self.store.update(run['id'], status='failed', calls=[], error='server_restarted')

    async def close(self):
        jobs = list(self.jobs.values())
        for job in jobs: job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
