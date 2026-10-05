"""Durable single-agent runs. Device operations happen only on Android."""
import asyncio
from contextlib import asynccontextmanager
from functools import partial
import logging
from pathlib import Path
import time
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from langsmith import tracing_context
from agent_model import ModelFailure, generate
from agent_prompts import SYSTEM, selection_policy
from agent_memory import context, last_turn
from agent_graph import AgentGraph
from task_store import Store
from task_state import State, TaskConflict, TERMINAL, MAX_TOOL_CALLS, MAX_MODEL_ROUNDS, READ_ONLY
from tool_dispatch import ToolDispatcher
from selection_policy import selection_identity_refresh, playback_preflight, episode_selection_question


class AgentTasks:
    def __init__(self, app, settings, saver, store, model=None):
        self.settings, self.store = settings, store
        self.saver = saver
        self.deletion_lock = asyncio.Lock()
        # Preserve the injectable model's existing signature for callers/tests.
        if model is None:
            self.model = partial(generate, client=app.state.client, gate=app.state.gate)
        else:
            self.model = partial(model, app)
        self.jobs = {}
        self.dispatcher = ToolDispatcher(store, self.get_run, self.jobs, self.launch)
        self.graph = AgentGraph(settings, store, self.model, self.get_run).compile(saver)

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
                    result = await self.graph.ainvoke(value, {'configurable': {'thread_id': run_id}, 'recursion_limit': 64})
                if result.get('__interrupt__'):
                    results = self.advance_batch(run_id, result['calls'])
                    if results is None:
                        break
                    value = Command(resume=results)
                else:
                    self.store.update(run_id, status='completed', calls=[], text=result['messages'][-1].get('content') or '',
                                      history=last_turn(result['messages']), memory=result.get('memory', {}), error='', retry_after=0)
                    break
        except asyncio.CancelledError:
            # Explicit cancellation and service shutdown choose their status outside this task.
            raise
        except ModelFailure as exc:
            self.store.update(run_id, status='failed', calls=[], error=exc.code, retry_after=exc.delay)
        except Exception as exc:
            logging.getLogger('maoer.agent').warning('agent_run=%s failure_type=%s', run_id, type(exc).__name__)
            self.store.update(run_id, status='failed', calls=[], error='invalid_agent_response')
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
                   'summary': {}, 'summary_pending': 0, 'extended_episodes': extended}
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
        return self.dispatcher.submit(run_id, results)

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
        if run['error'] == 'tool_round_limit': raise TaskConflict('task_not_retryable')
        with tracing_context(enabled=False):
            snapshot = await self.graph.aget_state({'configurable': {'thread_id': run_id}})
        # Deletion may begin while the checkpointer read is awaited.
        try: run = self.get_run(run_id)
        except KeyError: raise TaskConflict('conversation_deleted') from None
        if run['status'] != 'failed' or run_id in self.jobs: raise TaskConflict('task_not_retryable')
        if not snapshot.next or any(task.interrupts for task in snapshot.tasks):
            raise TaskConflict('task_not_retryable')
        run = self.store.update(run_id, status='generating', text='', error='', retry_after=0)
        self.launch(run_id, None)
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
                                  text=messages[-1].get('content') or '', history=last_turn(messages),
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


@asynccontextmanager
async def task_service(app, settings, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    store = Store(path)
    try:
        # A synchronous snapshot write must never block the event loop waiting
        # for an async checkpointer transaction in the same SQLite database.
        async with AsyncSqliteSaver.from_conn_string(str(path) + '.checkpoints') as saver:
            tasks = AgentTasks(app, settings, saver, store)
            await tasks.recover()
            try: yield tasks
            finally: await tasks.close()
    finally:
        store.close()
