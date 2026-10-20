"""OpenAI-compatible provider mapping and outbound HTTP, shared by both callers."""
from urllib.parse import urlsplit
import httpx
from maoer_agent.model.transport import limited_upstream
from maoer_agent.model.rate_limit import upstream_failure_code


def request_body(payload, settings, *, agent=False):
    if payload.model is not None and payload.model != settings.model:
        raise ValueError('Only the configured model is enabled')
    body = {**payload.model_dump(exclude_none=True), 'model': settings.model}
    if urlsplit(settings.base_url).hostname == 'api.deepseek.com' or settings.model in (
        'deepseek-flash', 'deepseek-v4.1-flash',
    ):
        body['thinking'] = {'type': 'disabled'}
    if not agent:
        return body
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
    return body


async def open_response(client, settings, body):
    request = client.build_request('POST', settings.endpoint,
        headers={'Authorization': 'Bearer ' + settings.api_key}, json=body)
    return await client.send(request, stream=True)


async def response_failure(response, gate):
    if response.status_code == 200:
        return None
    try:
        data = await limited_upstream(response, 16384)
    except (ValueError, UnicodeError, httpx.HTTPError):
        data = {}
    if response.status_code == 429:
        return gate.limiter.record(data, response.headers.get('Retry-After'))
    return upstream_failure_code(response.status_code, data)
