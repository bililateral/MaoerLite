"""Admission and upstream cooldown shared by both model entry points."""
from collections import deque
import time
from rate_limit import RateLimitPolicy


class Gate:
    """One process, one event loop; no awaits inside state transitions."""
    def __init__(self, requests_per_minute=0):
        self.requests_per_minute = requests_per_minute
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
        if self.requests_per_minute and len(self.times) >= self.requests_per_minute:
            return 'local_rate_limit', max(1, int(61 - (now - self.times[0])))
        if self.requests_per_minute:
            self.times.append(now)
        self.active = True
        return None

    def leave(self):
        self.active = False
