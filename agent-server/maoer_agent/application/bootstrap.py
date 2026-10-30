"""Application composition and resource lifetime; runtime does not construct services."""
from contextlib import asynccontextmanager, AsyncExitStack, ExitStack
from functools import partial
from pathlib import Path
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from maoer_agent.agent.tasks import AgentTasks
from maoer_agent.model.client import generate
from maoer_agent.model.embeddings import LocalEmbeddings
from maoer_agent.research.relevance import ModelRelevanceJudge, ModelWebRelevanceJudge
from maoer_agent.storage.tasks import Store
from maoer_agent.research.store import ResearchStore
from maoer_agent.research.rss import RssResearch, CATALOG
from maoer_agent.research.web import WebResearch
from maoer_agent.research.tools import ResearchTools
from maoer_agent.research.updates import restore_snapshot_scopes
from maoer_agent.application.sources import SourceQueries
from maoer_agent.application.replies import page_reply, query_reply


@asynccontextmanager
async def task_service(settings, client, gate, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as storage:
        store = Store(path)
        storage.callback(store.close)
        knowledge = ResearchStore(path.parent / 'research.sqlite3')
        storage.callback(knowledge.close)
        references = {}
        for run in store.runs():
            snapshots = references.setdefault(run['conversation_id'], set())
            results = [r['result'] for r in run.get('results', [])] + list(run.get('raw_results', {}).values())
            snapshots.update(r['query_snapshot'] for r in results if r.get('query_snapshot'))
        restore_snapshot_scopes(knowledge, references)
        async with AsyncExitStack() as services:
            rss = RssResearch(knowledge, client, LocalEmbeddings(path.parent / 'embedding-model'),
                              ModelRelevanceJudge(settings.model, settings.context_budget_bytes))
            web = WebResearch(settings, client, knowledge)
            services.push_async_callback(web.close)
            services.push_async_callback(rss.close)
            saver = await services.enter_async_context(AsyncSqliteSaver.from_conn_string(str(path) + '.checkpoints'))
            research = ResearchTools(knowledge, rss, web,
                ModelWebRelevanceJudge(settings.model, settings.context_budget_bytes))
            tasks = AgentTasks(settings, saver, store, partial(generate, client=client, gate=gate), research, CATALOG, page_reply, query_reply,
                               query_scope_cleanup=knowledge.release_scope)
            services.push_async_callback(tasks.close)
            rss.start()
            await tasks.recover()
            yield tasks, SourceQueries(tasks.get_run, knowledge.read)
