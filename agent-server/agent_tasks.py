"""Durable single-agent runs. Device operations happen only on Android."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langsmith import tracing_context
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import set_config_context

from agent_model import ModelFailure, generate
from agent_tools import DEFINITIONS, SYSTEM, validated_calls

TERMINAL = {'completed', 'cancelled', 'failed'}


class State(TypedDict):
    run_id: str
    messages: list[dict]
    rounds: int
    calls: list[dict]


class TaskConflict(Exception):
    pass


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS agent_runs (id TEXT PRIMARY KEY, conversation TEXT NOT NULL, body TEXT NOT NULL)')
        self.db.execute('CREATE INDEX IF NOT EXISTS agent_conversation ON agent_runs(conversation)')
        self.db.execute('CREATE TABLE IF NOT EXISTS agent_deleted_conversations (id_hash TEXT PRIMARY KEY)')
        self.db.commit()

    def get(self, run_id):
        row = self.db.execute('SELECT body FROM agent_runs WHERE id=?', (run_id,)).fetchone()
        if row is None: raise KeyError(run_id)
        return json.loads(row[0])

    def put(self, run):
        run['version'] = run.get('version', 0) + 1
        self.db.execute('INSERT OR REPLACE INTO agent_runs VALUES (?,?,?)',
                        (run['id'], run['conversation_id'], json.dumps(run, ensure_ascii=False)))
        self.db.commit()
        return run

    def update(self, run_id, **fields):
        return self.put({**self.get(run_id), **fields})

    def conversation(self, conversation):
        return [json.loads(r[0]) for r in self.db.execute(
            'SELECT body FROM agent_runs WHERE conversation=? ORDER BY rowid', (conversation,))]

    def deleted(self, conversation):
        digest = hashlib.sha256(conversation.encode()).hexdigest()
        return self.db.execute('SELECT 1 FROM agent_deleted_conversations WHERE id_hash=?', (digest,)).fetchone() is not None

    def mark_deleted(self, conversation):
        # Keep no message content, only a fingerprint that rejects stale replays.
        digest = hashlib.sha256(conversation.encode()).hexdigest()
        self.db.execute('INSERT OR IGNORE INTO agent_deleted_conversations VALUES (?)', (digest,))
        self.db.commit()

    def remove_conversation(self, conversation):
        self.db.execute('DELETE FROM agent_runs WHERE conversation=?', (conversation,))
        self.db.commit()

    @staticmethod
    def public(run):
        return {k: v for k, v in run.items() if k not in ('history', 'receipts', 'input')}


class AgentTasks:
    def __init__(self, app, settings, saver, store, model=None):
        self.app, self.settings, self.store = app, settings, store
        self.saver = saver
        self.deletion_lock = asyncio.Lock()
        self.model = model or generate
        self.jobs = {}
        graph = StateGraph(State)
        graph.add_node('model', self.model_node)
        graph.add_node('device', self.device_node)
        graph.add_edge(START, 'model')
        graph.add_conditional_edges('model', lambda state: 'device' if state['calls'] else END)
        graph.add_edge('device', 'model')
        self.graph = graph.compile(checkpointer=saver)

    async def model_node(self, state):
        if state['rounds'] >= 5:
            raise ModelFailure('tool_round_limit')
        run_id = state['run_id']
        started, waited = time.monotonic(), 0
        async def publish(text):
            self.store.update(run_id, text=text, status='generating', retry_after=0)
        for attempt in range(3):
            self.store.update(run_id, text='', status='generating', retry_after=0, error='')
            try:
                message = await self.model(self.app, self.settings, state['messages'], DEFINITIONS, publish)
                break
            except ModelFailure as exc:
                if (not exc.retryable or attempt == 2 or waited + exc.delay > 120
                        or time.monotonic() - started + exc.delay >= 180):
                    raise
                self.store.update(run_id, status='retry_wait', retry_after=exc.delay, error=exc.code,
                                  retry_at=int(time.time() + exc.delay))
                await asyncio.sleep(exc.delay)
                waited += exc.delay
        calls = validated_calls(message)
        if calls:
            # No tool is dispatched until complete model output and validation.
            known = []
            for previous in state['messages']:
                if previous['role'] == 'tool':
                    result = json.loads(previous['content'])
                    known.extend(result.get('items', []))
            for call in calls:
                args = call['arguments']
                if call['name'] == 'list_episodes' and not any(
                        item.get('id') == args['podcast_id'] and item.get('kind') == 'podcast' for item in known):
                    raise ModelFailure('unverified_podcast_id')
                if call['name'] == 'play_episode' and not any(
                        item.get('id') == args['episode_id'] and item.get('podcast_id') == args['podcast_id']
                        and item.get('kind') == 'episode' for item in known):
                    raise ModelFailure('unverified_episode_id')
            ids = [c['id'] for c in calls]
            seen = {c['id'] for m in state['messages'] for c in m.get('tool_calls', [])}
            if any(not i or i in seen for i in ids):
                raise ModelFailure('invalid_tool_call')
        return {'messages': state['messages'] + [message], 'rounds': state['rounds'] + 1, 'calls': calls}

    def device_node(self, state, config: RunnableConfig):
        # No side effects before interrupt: this node restarts on resume.
        # Python 3.10 does not propagate Runnable context through async tasks.
        # Explicitly use the supplied public config/context for this sync node.
        with set_config_context(config) as context:
            results = context.run(interrupt, {'calls': state['calls']})
        messages = state['messages'] + [{'role': 'tool', 'tool_call_id': r['call_id'],
                                       'content': json.dumps(r['result'], ensure_ascii=False)} for r in results]
        return {'messages': messages, 'calls': []}

    def launch(self, run_id, value):
        if run_id in self.jobs: raise TaskConflict('task_busy')
        self.jobs[run_id] = asyncio.create_task(self.drive(run_id, value))

    async def drive(self, run_id, value):
        try:
            # Keep conversation data local even if the shell has tracing env vars.
            with tracing_context(enabled=False):
                result = await self.graph.ainvoke(value, {'configurable': {'thread_id': run_id}, 'recursion_limit': 16})
            if result.get('__interrupt__'):
                self.store.update(run_id, status='awaiting_tools', calls=result['calls'], text='', retry_after=0)
            else:
                self.store.update(run_id, status='completed', calls=[], text=result['messages'][-1].get('content') or '',
                                  history=result['messages'], error='', retry_after=0)
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
        try:
            existing = self.store.get(request.id)
            if existing['input'] != value: raise TaskConflict('request_id_reused')
            return existing
        except KeyError:
            pass
        previous = self.store.conversation(request.conversation_id)
        if any(r['status'] not in TERMINAL for r in previous): raise TaskConflict('conversation_busy')
        completed = [r for r in previous if r['status'] == 'completed']
        history = completed[-1]['history'][1:] if completed else []
        # Keep complete user/tool groups, never truncate inside a tool round.
        starts = [i for i, message in enumerate(history) if message['role'] == 'user']
        if len(starts) > 1: history = history[starts[-1]:]
        messages = [{'role': 'system', 'content': SYSTEM}] + history + [{'role': 'user', 'content': request.message}]
        initial = {'run_id': request.id, 'messages': messages, 'rounds': 0, 'calls': []}
        run = self.store.put({'id': request.id, 'conversation_id': request.conversation_id, 'status': 'generating',
            'text': '', 'calls': [], 'results': [], 'claimed': [], 'receipts': {}, 'error': '', 'retry_after': 0,
            'retry_at': 0, 'version': 0, 'input': value, 'history': [], 'created_at': int(time.time())})
        self.launch(request.id, initial)
        return run

    def get_run(self, run_id):
        run = self.store.get(run_id)
        if self.store.deleted(run['conversation_id']): raise KeyError(run_id)
        return run

    def claim(self, run_id, call_id):
        run = self.get_run(run_id)
        if run['status'] != 'awaiting_tools' or call_id not in [c['id'] for c in run['calls']]:
            raise TaskConflict('tool_not_pending')
        if call_id not in run['claimed']:
            run['claimed'].append(call_id)
            self.store.put(run)
        return run

    def submit(self, run_id, results):
        run = self.get_run(run_id)
        fingerprint = hashlib.sha256(json.dumps(results, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        key = ','.join(r['call_id'] for r in results)
        if key in run['receipts']:
            if run['receipts'][key] != fingerprint: raise TaskConflict('tool_result_changed')
            return run
        if run['status'] != 'awaiting_tools' or run_id in self.jobs: raise TaskConflict('tool_not_pending')
        if [r['call_id'] for r in results] != [c['id'] for c in run['calls']]: raise TaskConflict('tool_id_mismatch')
        if any(r['call_id'] not in run['claimed'] for r in results): raise TaskConflict('tool_not_claimed')
        run['receipts'][key] = fingerprint
        run['results'].extend(results)
        run['status'], run['calls'] = 'generating', []
        self.store.put(run)
        self.launch(run_id, Command(resume=results))
        return run

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
        conversations = [r[0] for r in self.store.db.execute('SELECT DISTINCT conversation FROM agent_runs')]
        for conversation in conversations:
            if self.store.deleted(conversation):
                await self.delete_conversation(conversation)
        for (body,) in self.store.db.execute('SELECT body FROM agent_runs').fetchall():
            run = json.loads(body)
            if run['status'] in TERMINAL: continue
            with tracing_context(enabled=False):
                snapshot = await self.graph.aget_state({'configurable': {'thread_id': run['id']}})
            messages = snapshot.values.get('messages', [])
            if (not snapshot.next and messages and messages[-1].get('role') == 'assistant'
                    and not messages[-1].get('tool_calls') and not snapshot.values.get('calls')):
                # The final checkpoint can commit before the public snapshot.
                # Recover its answer without another model call or device effect.
                self.store.update(run['id'], status='completed', calls=[],
                                  text=messages[-1].get('content') or '', history=messages,
                                  error='', retry_after=0)
            elif snapshot.tasks and any(task.interrupts for task in snapshot.tasks):
                calls = snapshot.values.get('calls', [])
                key = ','.join(c['id'] for c in calls)
                if key in run['receipts']:
                    results = [r for r in run['results'] if r['call_id'] in {c['id'] for c in calls}]
                    self.launch(run['id'], Command(resume=results))
                else:
                    self.store.update(run['id'], status='awaiting_tools', calls=calls, text='')
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
        store.db.close()
