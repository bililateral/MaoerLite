"""Bounded HTTP and SSE reads without a dependency on FastAPI application state."""
import asyncio
import json
import time

MAX_BODY = 65536
TOTAL_TIMEOUT = 90


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
