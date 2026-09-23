"""Agent model adapter reuses the gateway's HTTP client and admission policy."""
import asyncio
import json
import time
import httpx
from pydantic import Field, model_validator
from protocol import ChatRequest, Message
from rate_limit import Throttle, upstream_failure_code


class ModelFailure(Exception):
    def __init__(self, code, retryable=False, delay=0):
        self.code, self.retryable, self.delay = code, retryable, delay
        super().__init__(code)


class AgentMessage(Message):
    # Kept only in server checkpoints/history; never streamed to the device.
    reasoning_content: str | None = Field(default=None, max_length=64000, repr=False)

    @model_validator(mode='after')
    def reasoning_role(self):
        if self.reasoning_content is not None and self.role != 'assistant':
            raise ValueError('Only assistant messages can contain reasoning')
        return self


class AgentChatRequest(ChatRequest):
    # 24 sequential tool rounds plus bounded history and the final answer.
    messages: list[AgentMessage] = Field(min_length=1, max_length=160)
    max_tokens: int = Field(default=4096, ge=1, le=8192)


async def generate(app, settings, messages, tools, publish):
    from app import limited_upstream, sse_data, TOTAL_TIMEOUT
    payload = AgentChatRequest.model_validate({'messages': messages, 'tools': tools or None,
                                              'tool_choice': 'auto' if tools else None,
                                              'stream': True, 'max_tokens': 8192 if settings.agent_thinking else 4096})
    body = payload.upstream_body(settings)
    if settings.agent_thinking:
        body['thinking'] = {'type': 'enabled'}
        body['reasoning_effort'] = settings.reasoning_effort
        body.pop('tool_choice', None)
        for message in body['messages']:
            if message['role'] == 'assistant':
                message.setdefault('reasoning_content', '')
                message['content'] = message.get('content') or ''
    else:
        for message in body['messages']:
            message.pop('reasoning_content', None)
    gate = app.state.gate
    refusal = gate.enter()
    if isinstance(refusal, Throttle):
        raise ModelFailure(refusal.code, refusal.retryable, refusal.retry_after)
    if refusal:
        raise ModelFailure(refusal[0], True, refusal[1])
    response = None
    started = time.monotonic()
    text, reasoning, calls = '', '', {}
    try:
        async def read():
            nonlocal response, text, reasoning
            request = app.state.client.build_request('POST', settings.endpoint,
                headers={'Authorization': 'Bearer ' + settings.api_key}, json=body)
            response = await app.state.client.send(request, stream=True)
            if response.status_code == 429:
                try:
                    data = await limited_upstream(response, 16384)
                except (ValueError, UnicodeError, httpx.HTTPError):
                    data = {}
                limit = gate.limiter.record(data, response.headers.get('Retry-After'))
                raise ModelFailure(limit.code, limit.retryable, limit.retry_after)
            if response.status_code != 200:
                try:
                    data = await limited_upstream(response, 16384)
                except (ValueError, UnicodeError, httpx.HTTPError):
                    data = {}
                raise ModelFailure(upstream_failure_code(response.status_code, data))
            finish = None
            async for raw in sse_data(response, started + TOTAL_TIMEOUT):
                if raw == '[DONE]':
                    if finish not in ('stop', 'tool_calls') or (not text and not calls):
                        raise ModelFailure('incomplete_model_response')
                    gate.limiter.reset()
                    result = {'role': 'assistant', 'content': text or None}
                    if settings.agent_thinking:
                        result['reasoning_content'] = reasoning
                    if calls:
                        result['tool_calls'] = [calls[k] for k in sorted(calls)]
                    return result
                data = json.loads(raw)
                for choice in data.get('choices', []):
                    if choice.get('index', 0) != 0:
                        continue
                    finish = choice.get('finish_reason') or finish
                    delta = choice.get('delta', {})
                    reasoning += delta.get('reasoning_content') or ''
                    if len(reasoning) > 64000:
                        raise ModelFailure('model_response_too_large')
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
