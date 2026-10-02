"""Durable single-agent runs. Device operations happen only on Android."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import time
from dataclasses import replace
from typing import TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langsmith import tracing_context
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import set_config_context

from agent_model import ModelFailure, generate
from agent_tools import SYSTEM, definitions, validated_calls
from agent_memory import context, last_turn, model_messages, references, remember, replay_references, PLAYBACK_SNAPSHOT_TOOLS
from agent_summary import (MAX_BATCHES, SUMMARY_TIMEOUT, batch, parse_summary,
                           summary_messages, with_summary)

TERMINAL = {'completed', 'cancelled', 'failed'}
MAX_TOOL_CALLS = 24
MAX_MODEL_ROUNDS = MAX_TOOL_CALLS + 1
READ_ONLY = {'search_catalog', 'list_episodes', 'get_playback_state', 'get_podcast_details', 'search_podcast_content'}


class State(TypedDict):
    run_id: str
    messages: list[dict]
    rounds: int
    calls: list[dict]
    memory: dict
    tool_count: int
    final_only: bool
    summary_candidates: list[str]
    summary: dict
    summary_pending: int
    extended_episodes: bool


class TaskConflict(Exception):
    pass


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
        for call in message.get('tool_calls', []):
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
    prior_play = any(c['function']['name'] == 'play_episode'
                     for m in previous for c in m.get('tool_calls', []))
    if not prior_play:
        for message in previous:
            for call in message.get('tool_calls', []):
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
        for call in message.get('tool_calls', []):
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
            order_name = {'source': '原始列表', 'reverse_source': '原始列表从末尾往前数', 'newest': '从新到旧列表'}[order]
            return f'{order_name}没有第{number}条，但找到了标题期号为{number}的《{title}》。你是想播放这期吗？目前没有开始新的播放。'
        alternative = alternatives.get((args['podcast_id'], args['episode_id']))
        if alternative and (args['podcast_id'], alternative[0]) in missing_numbers:
            position, order, title = alternative
            order_name = {'source': '原始列表', 'reverse_source': '原始列表从末尾往前数', 'newest': '从新到旧列表'}[order]
            return f'没有找到标题期号为{position}的分集。{order_name}第{position}条是《{title}》。你是想播放这条吗？目前没有开始新的播放。'
        selected = positioned.get((args['podcast_id'], args['episode_id']))
        if selected:
            position, order, title = selected
            if (args['podcast_id'], position, order, args['episode_id']) not in shown:
                order_name = {'source': '原始列表', 'reverse_source': '原始列表从末尾往前数', 'newest': '从新到旧列表'}[order]
                return f'{order_name}第{position}条是《{title}》。确认播放这一条吗？目前没有开始新的播放。'
    return None


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS agent_runs (id TEXT PRIMARY KEY, conversation TEXT NOT NULL, body TEXT NOT NULL)')
        self.db.execute('CREATE INDEX IF NOT EXISTS agent_conversation ON agent_runs(conversation)')
        self.db.execute('CREATE TABLE IF NOT EXISTS agent_deleted_conversations (id_hash TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE IF NOT EXISTS agent_summaries (conversation TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.commit()

    def get(self, run_id):
        row = self.db.execute('SELECT body FROM agent_runs WHERE id=?', (run_id,)).fetchone()
        if row is None: raise KeyError(run_id)
        return json.loads(row[0])

    def put(self, run):
        run['version'] = run.get('version', 0) + 1
        # Keep creation order stable when an older task is retried or updated.
        self.db.execute('INSERT INTO agent_runs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
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
        self.db.execute('DELETE FROM agent_summaries WHERE conversation=?', (conversation,))
        self.db.commit()

    def summary(self, conversation):
        row = self.db.execute('SELECT body FROM agent_summaries WHERE conversation=?', (conversation,)).fetchone()
        return json.loads(row[0]) if row else {'data': {}, 'covered': []}

    def save_summary(self, conversation, value):
        self.db.execute('INSERT OR REPLACE INTO agent_summaries VALUES (?,?)', (conversation, json.dumps(value, ensure_ascii=False)))
        self.db.commit()

    @staticmethod
    def public(run):
        return {k: v for k, v in run.items() if k not in ('history', 'receipts', 'input', 'memory', 'batch', 'tool_ledger', 'summary_error')}


class AgentTasks:
    def __init__(self, app, settings, saver, store, model=None):
        self.app, self.settings, self.store = app, settings, store
        self.saver = saver
        self.deletion_lock = asyncio.Lock()
        self.model = model or generate
        self.jobs = {}
        graph = StateGraph(State)
        graph.add_node('prepare', self.prepare_node)
        graph.add_node('model', self.model_node)
        graph.add_node('device', self.device_node)
        graph.add_edge(START, 'prepare')
        graph.add_edge('prepare', 'model')
        graph.add_conditional_edges('model', lambda state: 'device' if state['calls'] else
                                    ('model' if state['messages'][-1]['role'] == 'tool' else END))
        graph.add_edge('device', 'model')
        self.graph = graph.compile(checkpointer=saver)

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
                message = await asyncio.wait_for(self.model(self.app, replace(self.settings, agent_thinking=False),
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
        messages = [*messages]
        messages[0] = {**messages[0], 'content': messages[0]['content'] +
            '\n当前服务发送给API的model标识是' + json.dumps(self.settings.model, ensure_ascii=False) +
            '。这是部署配置事实，点播助手是应用角色，两者不矛盾。用户询问模型时如实区分配置名称与供应商实际后端；不能仅因自己的应用角色就否认此模型配置，也不能凭配置独立认证中转站底层模型身份。'}
        # Explicit per-turn evidence prevents old device snapshots being mistaken
        # for a fresh read. This is instruction context, never a canned user reply.
        current = last_turn(state['messages'])
        if not any(m.get('role') == 'tool' for m in current):
            messages = [*messages]
            messages[0] = {**messages[0], 'content': messages[0]['content'] +
                '\n本轮尚未执行任何手机工具。历史状态不是本轮核对结果；用户要求“再次核对/再查一次”且上下文指向播放器时，必须先get_playback_state，不能直接说已核对，即使只要一句话也必须查询。'}
        if final_only:
            messages = [*messages]
            messages[0] = {**messages[0], 'content': messages[0]['content'] +
                           '\n工具预算已用尽。不要调用工具，依据已有真实结果总结，明确未完成事项。'}
        started, waited = time.monotonic(), 0
        async def publish(text):
            self.store.update(run_id, text=text, status='generating', retry_after=0)
        for attempt in range(3):
            self.store.update(run_id, text='', status='generating', retry_after=0, error='')
            try:
                message = await self.model(self.app, self.settings,
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
            clarification = episode_selection_question(state['messages'], calls)
            if clarification:
                return {'messages': state['messages'] + [{'role': 'assistant', 'content': clarification}],
                        'rounds': state['rounds'] + 1, 'calls': [], 'tool_count': state.get('tool_count', 0)}
            refresh = playback_preflight(state['messages'], calls, run_id, state['rounds'])
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
        return {'messages': state['messages'] + [message], 'rounds': state['rounds'] + 1,
                'calls': calls, 'tool_count': count + len(calls)}

    def device_node(self, state, config: RunnableConfig):
        # No side effects before interrupt: this node restarts on resume.
        # Python 3.10 does not propagate Runnable context through async tasks.
        # Explicitly use the supplied public config/context for this sync node.
        with set_config_context(config) as context:
            results = context.run(interrupt, {'calls': state['calls']})
        messages = state['messages'] + [{'role': 'tool', 'tool_call_id': r['call_id'],
                                       'content': json.dumps(r['result'], ensure_ascii=False)} for r in results]
        memory = state.get('memory')
        if memory is None:
            memory = replay_references({'turn': 1, 'groups': []}, state['run_id'], state['messages'])
        memory = remember(memory, state['run_id'], state['calls'], results)
        return {'messages': messages, 'calls': [], 'memory': memory}

    def launch(self, run_id, value):
        if run_id in self.jobs: raise TaskConflict('task_busy')
        self.jobs[run_id] = asyncio.create_task(self.drive(run_id, value))

    def advance_batch(self, run_id, calls):
        """Persist one pending device call, or return a complete ordered batch.

        The graph stays interrupted for the whole model batch. Every receipt
        commits separately, so a restart can reconstruct the cursor without
        asking the phone to execute a completed operation again.
        """
        run = self.get_run(run_id)
        by_id = {r['call_id']: r for r in run['results']}
        run['batch'] = calls
        ledger = {c['id']: c for c in run.get('tool_ledger', [])}
        ledger.update({c['id']: c for c in calls})
        run['tool_ledger'] = list(ledger.values())
        for index, call in enumerate(calls):
            if call['id'] in by_id:
                continue
            if run.get('control_blocked') and call['name'] not in READ_ONLY:
                result = {'call_id': call['id'], 'result': {
                    'status': 'error', 'executed': False, 'reason': 'previous_control_not_confirmed'}}
                run['results'].append(result)
                by_id[call['id']] = result
                continue
            run.update(status='awaiting_tools', calls=[call], text='', retry_after=0,
                       batch_completed=index, batch_total=len(calls))
            self.store.put(run)
            return None
        run.update(status='generating', calls=[], batch_completed=len(calls), batch_total=len(calls))
        self.store.put(run)
        return [by_id[c['id']] for c in calls]

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
        selection_policy = ('\n本客户端支持分集分页与位置选择。用户说第N集/第一集时用position=N、order=reverse_source，从原始RSS列表末尾往前数，第一集就是原始列表最后一条；不需要日期完整，也不按发布日期重排或匹配标题编号。首次找到后展示真实标题并问一次确认，下一轮确认后使用已展示ID播放，不反复确认。越界或查询失败不自动改查标题期号；明确第N期/标题期号N才用episode_number=N。明确原始列表第N条用source，从最新数用newest；当前页面排序未知时先澄清，不声称看到了页面。只要求查询或展示时不播放，也不强行询问是否播放。'
            if extended else '\n本客户端仅支持标题查询，不支持完整列表定位或分页。用户说第N集时说明需升级或请给出具体标题，不得以返回的前8条猜测末尾位置。不要传入未定义参数。')
        messages = [{'role': 'system', 'content': SYSTEM + selection_policy}] + history + [{'role': 'user', 'content': request.message}]
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
        run = self.get_run(run_id)
        if run['status'] != 'awaiting_tools' or call_id not in [c['id'] for c in run['calls']]:
            raise TaskConflict('tool_not_pending')
        if call_id not in run['claimed']:
            run['claimed'].append(call_id)
            self.store.put(run)
        return run

    def submit(self, run_id, results):
        run = self.get_run(run_id)
        if len(results) != 1: raise TaskConflict('single_result_required')
        fingerprint = hashlib.sha256(json.dumps(results, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        key = ','.join(r['call_id'] for r in results)
        if key in run['receipts']:
            if run['receipts'][key] != fingerprint: raise TaskConflict('tool_result_changed')
            return run
        if run['status'] == 'cancelled':
            # A claimed operation can finish just after cancellation. Preserve
            # its actual receipt, but never resume the graph or dispatch more.
            if key not in run['claimed'] or key not in {c['id'] for c in run.get('batch', [])}:
                raise TaskConflict('tool_not_pending')
            run['receipts'][key] = fingerprint
            run['results'].extend(results)
            return self.store.put(run)
        if run['status'] != 'awaiting_tools' or run_id in self.jobs: raise TaskConflict('tool_not_pending')
        if [r['call_id'] for r in results] != [c['id'] for c in run['calls']]: raise TaskConflict('tool_id_mismatch')
        if any(r['call_id'] not in run['claimed'] for r in results): raise TaskConflict('tool_not_claimed')
        run['receipts'][key] = fingerprint
        run['results'].extend(results)
        if run['calls'][0]['name'] not in READ_ONLY and results[0]['result'].get('status') != 'ok':
            run['control_blocked'] = True
        self.store.put(run)
        completed = self.advance_batch(run_id, run.get('batch', run['calls']))
        if completed is not None:
            self.launch(run_id, Command(resume=completed))
        return self.get_run(run_id)

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
        store.db.close()
