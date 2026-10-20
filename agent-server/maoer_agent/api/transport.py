"""Inbound request limits and caller disconnect cancellation."""
import asyncio

MAX_BODY = 65536
TOTAL_TIMEOUT = 90


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
