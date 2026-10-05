"""Private model gateway. No keys, prompts or upstream error bodies in logs."""
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import secrets
import time
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from config import ROOT, Settings
from protocol import ChatRequest, public_response
from rate_limit import Throttle, upstream_failure_code
from model_gate import Gate
from upstream_io import MAX_BODY, TOTAL_TIMEOUT, read_request, connected_operation, limited_upstream, sse_data

LOG = logging.getLogger('maoer.agent')


def error(status, code, request_id, retry_after=None, **details):
    headers = {'X-Request-Id': request_id, 'Cache-Control': 'no-store'}
    if retry_after:
        headers['Retry-After'] = str(retry_after)
    if retry_after is not None:
        details['retry_after'] = retry_after
    return JSONResponse({'error': {'code': code, 'request_id': request_id, **details}}, status_code=status, headers=headers)


def create_app(settings: Settings, transport=None, agent_db=None):
    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=15), follow_redirects=False,
            trust_env=False, transport=transport,
        ) as client:
            app.state.client = client
            from agent_tasks import task_service
            async with task_service(app, settings, agent_db or ROOT / '.local/agent-tasks.sqlite3') as tasks:
                app.state.agent = tasks
                yield

    app = FastAPI(title='Maoer Local Agent', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    gate = Gate(settings.model_requests_per_minute)
    app.state.gate = gate
    from agent_routes import register
    register(app, settings)

    @app.get('/healthz')
    async def health():
        return {'status': 'ok', 'model': settings.model, 'provider': settings.provider,
                'agent_thinking': settings.agent_thinking, 'reasoning_effort': settings.reasoning_effort if settings.agent_thinking else None,
                'scope': 'local' if settings.host.startswith('127.') else 'private-network', 'runtime': 'python-fastapi'}

    @app.post('/v1/chat/completions')
    async def chat(request: Request):
        request_id = str(uuid.uuid4())
        credential = request.headers.get('authorization', '').encode('utf-8')
        if not secrets.compare_digest(credential, ('Bearer ' + settings.access_token).encode()):
            return error(401, 'unauthorized', request_id)
        if request.headers.get('origin') is not None:
            return error(403, 'browser_origin_not_allowed', request_id)
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            return error(415, 'json_required', request_id)
        try:
            body = await asyncio.wait_for(read_request(request), 10)
            payload = ChatRequest.model_validate(json.loads(body))
            upstream_body = payload.upstream_body(settings)
        except OverflowError:
            return error(413, 'body_too_large', request_id)
        except asyncio.TimeoutError:
            return error(408, 'request_timeout', request_id)
        except (ValidationError, ValueError, UnicodeError, RecursionError):
            return error(400, 'invalid_request', request_id)
        refusal = gate.enter()
        if refusal:
            if isinstance(refusal, Throttle):
                return error(429, refusal.code, request_id, **refusal.details(), cooldown=True)
            return error(429, refusal[0], request_id, refusal[1],
                         retryable=refusal[0] == 'local_rate_limit', retry_after_source='local_gate')

        response, streaming = None, False
        started, outcome = time.monotonic(), 'failed'

        async def upstream():
            nonlocal response
            req = app.state.client.build_request('POST', settings.endpoint,
                headers={'Authorization': 'Bearer ' + settings.api_key}, json=upstream_body)
            response = await app.state.client.send(req, stream=True)
            if response.status_code == 429:
                try:
                    data = await limited_upstream(response, limit=16384)
                except (ValueError, UnicodeError, httpx.HTTPError):
                    data = {}
                return gate.limiter.record(data, response.headers.get('Retry-After'))
            if response.status_code != 200:
                try:
                    data = await limited_upstream(response, limit=16384)
                except (ValueError, UnicodeError, httpx.HTTPError):
                    data = {}
                return upstream_failure_code(response.status_code, data)
            if payload.stream:
                return None
            return public_response(await limited_upstream(response))

        async def finish():
            try:
                if response is not None:
                    await response.aclose()
            finally:
                gate.leave()
                LOG.info('request=%s result=%s elapsed_ms=%d', request_id, outcome, (time.monotonic() - started) * 1000)

        try:
            result = await connected_operation(request, upstream())
            if response.status_code != 200:
                if response.status_code == 429:
                    outcome = result.code
                    return error(429, outcome, request_id, **result.details(), cooldown=False)
                outcome = result
                return error(502, outcome, request_id)
            headers = {'X-Request-Id': request_id, 'Cache-Control': 'no-store'}
            if not payload.stream:
                if not result.get('choices'):
                    raise ValueError('Missing choices')
                outcome = 'ok'
                gate.limiter.reset()
                return JSONResponse(result, headers=headers)

            async def events():
                nonlocal outcome
                try:
                    async for data in sse_data(response, started + TOTAL_TIMEOUT):
                        if data == '[DONE]':
                            outcome = 'ok_stream'
                            gate.limiter.reset()
                            yield b'data: [DONE]\n\n'
                            return
                        clean = public_response(json.loads(data))
                        yield ('data: ' + json.dumps(clean, ensure_ascii=False) + '\n\n').encode('utf-8')
                    raise ValueError('Stream ended before DONE')
                except asyncio.CancelledError:
                    outcome = 'cancelled'
                    raise
                except Exception:
                    outcome = 'stream_interrupted'
                    yield ('event: error\ndata: ' + json.dumps({'error': {'code': outcome, 'request_id': request_id}}) + '\n\n').encode()
                finally:
                    await finish()

            headers['X-Accel-Buffering'] = 'no'
            streaming = True
            return StreamingResponse(events(), media_type='text/event-stream', headers=headers)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            outcome = 'upstream_timeout'
            return error(504, outcome, request_id)
        except (asyncio.CancelledError, ConnectionAbortedError):
            outcome = 'cancelled'
            raise
        except Exception:
            outcome = 'upstream_unavailable'
            return error(502, outcome, request_id)
        finally:
            if not streaming:
                await finish()

    return app
