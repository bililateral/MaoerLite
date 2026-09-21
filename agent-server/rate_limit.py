"""Sanitized upstream 429 classification and process-wide cooldown."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import re
import secrets
import time


@dataclass(frozen=True)
class Throttle:
    code: str
    upstream_code: str | None
    retryable: bool
    retry_after: int
    retry_after_source: str

    def details(self):
        return {k: v for k, v in vars(self).items() if k != 'code' and v is not None}


def retry_after_seconds(value, now=None):
    if not isinstance(value, str) or len(value) > 128:
        return None
    value = value.strip()
    if re.fullmatch(r'[0-9]{1,10}', value):
        return int(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        return max(0, math.ceil((date - (now or datetime.now(timezone.utc))).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


class RateLimitPolicy:
    def __init__(self, clock=time.monotonic, jitter=None):
        self.clock = clock
        self.jitter = jitter or (lambda: secrets.randbelow(6))
        self.failures = 0
        self.until = 0
        self.last = None

    def record(self, data, header):
        error = data.get('error') if isinstance(data, dict) else None
        raw = error.get('code') if isinstance(error, dict) else None
        upstream = str(raw) if type(raw) in (str, int) else ''
        upstream = upstream if re.fullmatch(r'[A-Za-z0-9_]{1,64}', upstream) else None
        mapping = {
            'rate_limit_exceeded': ('upstream_account_rate_limit', True),
            'overloaded_error': ('upstream_overloaded', True),
            'server_overloaded': ('upstream_overloaded', True),
            'insufficient_quota': ('upstream_quota_exhausted', False),
            'billing_hard_limit_reached': ('upstream_quota_exhausted', False),
            'invalid_api_key': ('upstream_account_restricted', False),
        }
        code, retryable = mapping.get(upstream, ('upstream_rate_limit_unknown', False))
        self.failures = min(self.failures + 1, 4)
        local = (30, 60, 120, 300)[self.failures - 1] + self.jitter()
        official = retry_after_seconds(header)
        delay = max(local, official or 0)
        source = 'local_backoff' if official is None else (
            'upstream' if official >= local else 'upstream_and_local_backoff')
        self.last = Throttle(code, upstream, retryable, delay, source)
        self.until = self.clock() + delay
        return self.last

    def pending(self):
        remaining = math.ceil(self.until - self.clock())
        if self.last is not None and remaining > 0:
            return replace(self.last, retry_after=remaining)
        return None

    def reset(self):
        self.failures, self.until, self.last = 0, 0, None
