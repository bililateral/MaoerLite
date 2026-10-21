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

from maoer_agent.core.config import ROOT, Settings
from maoer_agent.api.protocol import ChatRequest, public_response
from maoer_agent.model.rate_limit import Throttle
from maoer_agent.model.gate import Gate
from maoer_agent.model.failures import timeout_kind
from maoer_agent.api.transport import read_request, connected_operation
from maoer_agent.model.transport import TOTAL_TIMEOUT, limited_upstream, sse_data
from maoer_agent.model.provider import request_body, open_response, response_failure
from maoer_agent.model.network import model_transport

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
            trust_env=False, transport=transport if transport is not None else model_transport(settings),
        ) as client:
            app.state.client = client
            from maoer_agent.application.bootstrap import task_service
            async with task_service(settings, client, app.state.gate, agent_db or ROOT / '.local/agent-tasks.sqlite3') as (tasks, sources):
                app.state.agent = tasks
                app.state.source_queries = sources
                yield

    app = FastAPI(title='Maoer Local Agent', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    gate = Gate(settings.model_requests_per_minute)
    app.state.gate = gate
    from maoer_agent.api.routes import register
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
            upstream_body = request_body(payload, settings)
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
            response = await open_response(app.state.client, settings, upstream_body)
            failure = await response_failure(response, gate)
            if failure:
                return failure
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
                return error(502, outcome, request_id, http_status=response.status_code)
            headers = {'X-Request-Id': request_id, 'Cache-Control': 'no-store'}
            if not payload.stream:
                if not result.get('choices'):
                    raise ValueError('Missing choices')
                outcome = 'ok'
                gate.limiter.reset()
                return JSONResponse(result, headers=headers)

            async def events():
                nonlocal outcome
                received = False
                try:
                    async for data in sse_data(response, started + TOTAL_TIMEOUT):
                        received = True
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
                except Exception as exc:
                    timed_out = isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError))
                    outcome = 'upstream_timeout' if timed_out else 'stream_interrupted'
                    detail = {'timeout_kind': timeout_kind(exc, True, received), 'total_limit_seconds': TOTAL_TIMEOUT} if timed_out else {}
                    yield ('event: error\ndata: ' + json.dumps({'error': {'code': outcome, 'request_id': request_id, 'detail': detail}}) + '\n\n').encode()
                finally:
                    await finish()

            headers['X-Accel-Buffering'] = 'no'
            streaming = True
            return StreamingResponse(events(), media_type='text/event-stream', headers=headers)
        except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
            outcome = 'upstream_timeout'
            return error(504, outcome, request_id, detail={'timeout_kind': timeout_kind(exc, response is not None),
                         'elapsed_ms': round((time.monotonic() - started) * 1000), 'total_limit_seconds': TOTAL_TIMEOUT})
        except (asyncio.CancelledError, ConnectionAbortedError):
            outcome = 'cancelled'
            raise
        except httpx.ConnectError:
            outcome = 'upstream_connect_failed'
            return error(502, outcome, request_id)
        except httpx.HTTPError:
            outcome = 'upstream_unavailable'
            return error(502, outcome, request_id)
        except (ValueError, TypeError, KeyError):
            outcome = 'invalid_model_response'
            return error(502, outcome, request_id)
        except Exception as exc:
            outcome = 'invalid_agent_response'
            LOG.warning('request=%s failure_type=%s', request_id, type(exc).__name__)
            return error(500, outcome, request_id)
        finally:
            if not streaming:
                await finish()

    return app
