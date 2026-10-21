"""Authenticated task creation, snapshots, resumable SSE and device receipts."""
import asyncio
import json
import secrets
import re
import sqlite3
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field, ValidationError
from maoer_agent.contracts.chat import StrictModel
from maoer_agent.api.responses import run_response
from maoer_agent.application.sources import SourceNotFound
from maoer_agent.agent.state import TaskConflict, TERMINAL
from maoer_agent.api.transport import read_request


class NewRun(StrictModel):
    id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    conversation_id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    message: str = Field(min_length=1, max_length=2000)
    capabilities: list[str] = Field(default_factory=list, max_length=8)


def register(app, settings):
    async def authorized(request):
        actual = request.headers.get('authorization', '').encode()
        if not secrets.compare_digest(actual, ('Bearer ' + settings.access_token).encode()):
            return JSONResponse({'error': {'code': 'unauthorized'}}, 401)
        if request.headers.get('origin') is not None:
            return JSONResponse({'error': {'code': 'browser_origin_not_allowed'}}, 403)

    async def body(request):
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            raise ValueError('json_required')
        return json.loads(await asyncio.wait_for(read_request(request), 10))

    @app.exception_handler(TaskConflict)
    async def conflict(request, exc):
        return JSONResponse({'error': {'code': str(exc)}}, 409)

    @app.exception_handler(sqlite3.Error)
    async def storage_failure(request, exc):
        return JSONResponse({'error': {'code': 'server_storage_error'}}, 503)

    public = run_response

    @app.post('/v1/agent/runs')
    async def create(request: Request):
        refusal = await authorized(request)
        if refusal: return refusal
        try:
            data = NewRun.model_validate(await body(request))
        except asyncio.TimeoutError:
            return JSONResponse({'error': {'code': 'request_timeout'}}, 408)
        except OverflowError:
            return JSONResponse({'error': {'code': 'body_too_large'}}, 413)
        except (ValueError, ValidationError, RecursionError):
            return JSONResponse({'error': {'code': 'invalid_request'}}, 400)
        return public(app.state.agent.create(data))

    async def lookup(run_id, request):
        refusal = await authorized(request)
        if refusal: return refusal
        try: return app.state.agent.get_run(run_id)
        except KeyError: return JSONResponse({'error': {'code': 'run_not_found'}}, 404)

    @app.get('/v1/agent/runs/{run_id}/sources/{number}')
    async def source_text(run_id: str, number: int, request: Request):
        refusal = await authorized(request)
        if refusal: return refusal
        try:
            return app.state.source_queries.text(run_id, number)
        except KeyError:
            return JSONResponse({'error': {'code': 'run_not_found'}}, 404)
        except SourceNotFound:
            return JSONResponse({'error': {'code': 'source_not_found'}}, 404)

    @app.delete('/v1/agent/conversations/{conversation_id}')
    async def delete_conversation(conversation_id: str, request: Request):
        refusal = await authorized(request)
        if refusal: return refusal
        if not re.fullmatch(r'[a-f0-9-]{36}', conversation_id):
            return JSONResponse({'error': {'code': 'invalid_request'}}, 400)
        try:
            await app.state.agent.delete_conversation(conversation_id)
        except TaskConflict:
            raise
        except Exception:
            return JSONResponse({'error': {'code': 'conversation_delete_failed'}}, 503)
        return {'deleted': True, 'conversation_id': conversation_id}

    @app.get('/v1/agent/runs/{run_id}')
    async def get_run(run_id: str, request: Request):
        run = await lookup(run_id, request)
        return run if isinstance(run, JSONResponse) else public(run)

    @app.post('/v1/agent/runs/{run_id}/cancel')
    async def cancel(run_id: str, request: Request):
        run = await lookup(run_id, request)
        if isinstance(run, JSONResponse): return run
        return public(await app.state.agent.cancel(run_id))

    @app.post('/v1/agent/runs/{run_id}/claim/{call_id}')
    async def claim(run_id: str, call_id: str, request: Request):
        run = await lookup(run_id, request)
        if isinstance(run, JSONResponse): return run
        return public(app.state.agent.claim(run_id, call_id))

    @app.post('/v1/agent/runs/{run_id}/retry')
    async def retry(run_id: str, request: Request):
        run = await lookup(run_id, request)
        if isinstance(run, JSONResponse): return run
        return public(await app.state.agent.retry(run_id))

    @app.post('/v1/agent/runs/{run_id}/results')
    async def results(run_id: str, request: Request):
        run = await lookup(run_id, request)
        if isinstance(run, JSONResponse): return run
        try:
            data = await body(request)
            values = data['results']
            if not isinstance(values, list) or len(values) != 1: raise ValueError()
            for value in values:
                if not {'call_id', 'result'} <= set(value) or set(value) - {'call_id', 'result', 'recovered'} or not isinstance(value['call_id'], str): raise ValueError()
                if type(value.get('recovered', False)) is not bool: raise ValueError()
                result = value['result']
                if not isinstance(result, dict) or result.get('status') not in ('ok', 'error', 'unknown', 'accepted'): raise ValueError()
                if len(json.dumps(result, ensure_ascii=False)) > 12000: raise ValueError()
        except asyncio.TimeoutError:
            return JSONResponse({'error': {'code': 'request_timeout'}}, 408)
        except OverflowError:
            return JSONResponse({'error': {'code': 'body_too_large'}}, 413)
        except (KeyError, TypeError, ValueError, RecursionError):
            return JSONResponse({'error': {'code': 'invalid_tool_result'}}, 400)
        return public(app.state.agent.submit(run_id, values))

    @app.get('/v1/agent/runs/{run_id}/events')
    async def events(run_id: str, request: Request):
        run = await lookup(run_id, request)
        if isinstance(run, JSONResponse): return run
        async def stream():
            version, heartbeat = -1, 0
            while True:
                try: value = app.state.agent.get_run(run_id)
                except KeyError: return
                if value['version'] != version:
                    version = value['version']
                    yield 'data: ' + json.dumps(public(value), ensure_ascii=False) + '\n\n'
                if value['status'] in TERMINAL or value['status'] == 'awaiting_tools': return
                await asyncio.sleep(0.25)
                heartbeat += 1
                if heartbeat % 40 == 0: yield ': keep-alive\n\n'
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-store'})
