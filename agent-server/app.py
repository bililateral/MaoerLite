"""Private model gateway. No keys, prompts or upstream error bodies in logs."""
import asyncio
from collections import deque
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
from rate_limit import RateLimitPolicy, Throttle, upstream_failure_code

LOG = logging.getLogger('maoer.agent')
MAX_BODY = 65536
TOTAL_TIMEOUT = 90


def error(status, code, request_id, retry_after=None, **details):
    headers = {'X-Request-Id': request_id, 'Cache-Control': 'no-store'}
    if retry_after:
        headers['Retry-After'] = str(retry_after)
    if retry_after is not None:
        details['retry_after'] = retry_after
    return JSONResponse({'error': {'code': code, 'request_id': request_id, **details}}, status_code=status, headers=headers)


class Gate:
    """One process, one event loop; no awaits inside state transitions."""
    def __init__(self):
        self.active = False
        self.times = deque()
        self.limiter = RateLimitPolicy()

    def enter(self):
        now = time.monotonic()
        while self.times and now - self.times[0] >= 60:
            self.times.popleft()
        if self.active:
            return 'local_busy', 5
        cooldown = self.limiter.pending()
        if cooldown:
            return cooldown
        if len(self.times) >= 6:
            return 'local_rate_limit', max(1, int(61 - (now - self.times[0])))
        self.times.append(now)
        self.active = True
        return None

    def leave(self):
        self.active = False


async def read_request(request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            raise OverflowError
    return bytes(body)


async def watch_disconnect(request):
    # The request body has already been consumed. Wait for the ASGI disconnect
    # event directly so cancellation does not race an is_disconnected cancel scope.
    while True:
        message = await request.receive()
        if message['type'] == 'http.disconnect':
            return


async def connected_operation(request, operation):
    """Cancel upstream work when a non-streaming caller goes away."""
    work = asyncio.create_task(operation)
    disconnect = asyncio.create_task(watch_disconnect(request))
    try:
        done, _ = await asyncio.wait((work, disconnect), timeout=TOTAL_TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
        if work in done:
            return work.result()
        if disconnect in done:
            raise ConnectionAbortedError
        raise asyncio.TimeoutError
    finally:
        for task in (work, disconnect):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, disconnect, return_exceptions=True)


async def limited_upstream(response, limit=1_048_576):
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > limit:
            raise ValueError('Upstream response too large')
    return json.loads(body)


async def sse_data(response, deadline):
    """Bound stream size/line size and total time; accept multi-line SSE data."""
    iterator = response.aiter_bytes().__aiter__()
    buffer, data, total = b'', [], 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), remaining)
        except StopAsyncIteration:
            if buffer or data:
                raise ValueError('Incomplete SSE event')
            return
        total += len(chunk)
        if total > 2_097_152:
            raise ValueError('Stream too large')
        buffer += chunk
        while b'\n' in buffer:
            raw, buffer = buffer.split(b'\n', 1)
            if len(raw) > MAX_BODY:
                raise ValueError('SSE line too large')
            line = raw.rstrip(b'\r').decode('utf-8')
            if not line:
                if data:
                    yield '\n'.join(data)
                    data = []
            elif line.startswith('data:'):
                data.append(line[5:].lstrip(' '))
                if sum(map(len, data)) > MAX_BODY:
                    raise ValueError('SSE event too large')
        if len(buffer) > MAX_BODY:
            raise ValueError('SSE line too large')


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
    gate = Gate()
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
