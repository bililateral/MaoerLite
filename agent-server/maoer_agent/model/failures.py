"""Safe failure metadata: never include exception text, URLs, bodies or headers."""
import asyncio
import httpx


def timeout_kind(exc, response_received=False, stream_received=False):
    if isinstance(exc, httpx.ConnectTimeout): return 'connect'
    if isinstance(exc, httpx.WriteTimeout): return 'write'
    if isinstance(exc, httpx.PoolTimeout): return 'pool'
    if isinstance(exc, httpx.ReadTimeout):
        return 'stream_read' if stream_received else ('first_data' if response_received else 'response_headers')
    if isinstance(exc, asyncio.TimeoutError): return 'total'
    return 'unknown'
