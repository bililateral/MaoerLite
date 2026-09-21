"""Agent model adapter reuses the gateway's HTTP client and admission policy."""
import asyncio
import json
import time
import httpx
from protocol import ChatRequest
from rate_limit import Throttle


class ModelFailure(Exception):
    def __init__(self, code, retryable=False, delay=0):
        self.code, self.retryable, self.delay = code, retryable, delay
        super().__init__(code)


async def generate(app, settings, messages, tools, publish):
    from app import limited_upstream, sse_data, TOTAL_TIMEOUT
    payload = ChatRequest.model_validate({'messages': messages, 'tools': tools,
                                         'tool_choice': 'auto', 'stream': True, 'max_tokens': 1024})
    gate = app.state.gate
    refusal = gate.enter()
    if isinstance(refusal, Throttle):
        raise ModelFailure(refusal.code, refusal.retryable, refusal.retry_after)
    if refusal:
        raise ModelFailure(refusal[0], True, refusal[1])
    response = None
    started = time.monotonic()
    text, calls = '', {}
    try:
        async def read():
            nonlocal response, text
            request = app.state.client.build_request('POST', settings.endpoint,
                headers={'Authorization': 'Bearer ' + settings.api_key}, json=payload.upstream_body(settings))
            response = await app.state.client.send(request, stream=True)
            if response.status_code == 429:
                try:
                    data = await limited_upstream(response, 16384)
                except (ValueError, UnicodeError, httpx.HTTPError):
                    data = {}
                limit = gate.limiter.record(data, response.headers.get('Retry-After'))
                raise ModelFailure(limit.code, limit.retryable, limit.retry_after)
            if response.status_code != 200:
                raise ModelFailure('upstream_auth_failed' if response.status_code in (401, 403) else 'upstream_error')
            finish = None
            async for raw in sse_data(response, started + TOTAL_TIMEOUT):
                if raw == '[DONE]':
                    if finish not in ('stop', 'tool_calls') or (not text and not calls):
                        raise ModelFailure('incomplete_model_response')
                    gate.limiter.reset()
                    result = {'role': 'assistant', 'content': text or None}
                    if calls:
                        result['tool_calls'] = [calls[k] for k in sorted(calls)]
                    return result
                data = json.loads(raw)
                for choice in data.get('choices', []):
                    if choice.get('index', 0) != 0:
                        continue
                    finish = choice.get('finish_reason') or finish
                    delta = choice.get('delta', {})
                    text += delta.get('content') or ''
                    if len(text) > 16000:
                        raise ModelFailure('model_response_too_large')
                    if delta.get('content'):
                        await publish(text)
                    for part in delta.get('tool_calls') or []:
                        index = part.get('index', 0)
                        if type(index) is not int or not 0 <= index < 8:
                            raise ModelFailure('invalid_tool_call')
                        call = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                        if part.get('id'): call['id'] = part['id']
                        for key in ('name', 'arguments'):
                            call['function'][key] += part.get('function', {}).get(key) or ''
                        if len(call['function']['arguments']) > 16000:
                            raise ModelFailure('invalid_tool_call')
            raise ModelFailure('stream_interrupted')
        return await asyncio.wait_for(read(), TOTAL_TIMEOUT)
    except (httpx.TimeoutException, asyncio.TimeoutError):
        raise ModelFailure('upstream_timeout') from None
    except httpx.HTTPError:
        raise ModelFailure('upstream_unavailable') from None
    finally:
        try:
            if response is not None: await response.aclose()
        finally:
            gate.leave()
