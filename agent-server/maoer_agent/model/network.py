"""Endpoint-scoped source binding and DNS, without changing host networking."""
import asyncio
import secrets
import socket
import struct
import time

import httpx


def name_end(data, position):
    while position < len(data):
        length = data[position]
        if length & 0xc0 == 0xc0:
            if position + 2 > len(data):
                break
            return position + 2
        position += 1
        if not length:
            return position
        if length > 63:
            break
        position += length
    raise ValueError('Invalid DNS name')


def resolve_ipv4(host, source, resolver, timeout):
    identifier = secrets.token_bytes(2)
    question = b''.join(bytes([len(label)]) + label.encode('ascii') for label in host.split('.'))
    question += b'\0' + struct.pack('!HH', 1, 1)
    packet = identifier + struct.pack('!HHHHH', 0x0100, 1, 0, 0, 0) + question
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.bind((source, 0))
        connection.settimeout(timeout)
        connection.connect((resolver, 53))
        connection.send(packet)
        data = connection.recv(4096)
    if len(data) < 12 or data[:2] != identifier:
        raise ValueError('Invalid DNS response')
    flags, questions, answers = struct.unpack('!HHH', data[2:8])
    if not flags & 0x8000 or flags & 0x020f or questions != 1:
        raise ValueError('DNS lookup failed')
    position = name_end(data, 12) + 4
    addresses, lifetimes = [], []
    for record in range(answers):
        position = name_end(data, position)
        kind, record_class, lifetime, length = struct.unpack('!HHIH', data[position:position + 10])
        position += 10
        end = position + length
        if end > len(data):
            raise ValueError('Incomplete DNS answer')
        if record_class == 1 and kind in (1, 5):
            lifetimes.append(lifetime)
        if kind == 1 and record_class == 1 and length == 4:
            address = socket.inet_ntoa(data[position:end])
            if address not in addresses:
                addresses.append(address)
        position = end
    if not addresses:
        raise ValueError('DNS returned no IPv4 addresses')
    return addresses, min(min(lifetimes), 300)


class ModelNetworkTransport(httpx.AsyncBaseTransport):
    def __init__(self, endpoint, source, resolver):
        self.endpoint = httpx.URL(endpoint)
        self.source, self.resolver = source, resolver
        self.default = httpx.AsyncHTTPTransport(trust_env=False)
        self.direct = httpx.AsyncHTTPTransport(local_address=source, trust_env=False)
        self.addresses, self.expires = [], 0

    async def handle_async_request(self, request):
        if (request.url.scheme, request.url.host, request.url.port, request.url.path) != (
                self.endpoint.scheme, self.endpoint.host, self.endpoint.port, self.endpoint.path):
            return await self.default.handle_async_request(request)
        timeout = dict(request.extensions.get('timeout', {}))
        budget = timeout.get('connect')
        deadline = time.monotonic() + (15 if budget is None else budget)
        if not self.addresses or time.monotonic() >= self.expires:
            try:
                self.addresses, lifetime = await asyncio.to_thread(resolve_ipv4,
                    self.endpoint.raw_host.decode('ascii'), self.source, self.resolver,
                    min(3, max(0.001, deadline - time.monotonic())))
                self.expires = time.monotonic() + lifetime
            except TimeoutError:
                raise httpx.ConnectTimeout('Model DNS lookup timed out', request=request) from None
            except (OSError, ValueError, IndexError, struct.error):
                raise httpx.ConnectError('Model DNS lookup failed', request=request) from None
        for index, address in enumerate(self.addresses):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise httpx.ConnectTimeout('Model connection timed out', request=request)
            extensions = {**request.extensions,
                'timeout': {**timeout, 'connect': remaining},
                'sni_hostname': self.endpoint.raw_host.decode('ascii')}
            routed = httpx.Request(request.method, request.url.copy_with(host=address),
                headers=request.headers, stream=request.stream, extensions=extensions)
            try:
                return await self.direct.handle_async_request(routed)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if index == len(self.addresses) - 1:
                    self.expires = 0
                    raise

    async def aclose(self):
        try:
            await self.direct.aclose()
        finally:
            await self.default.aclose()


def model_transport(settings):
    if settings.model_source_ipv4:
        return ModelNetworkTransport(settings.endpoint, settings.model_source_ipv4, settings.model_dns_ipv4)
    return None
