import time
from collections import defaultdict, deque


class SlidingWindow:
    """In-process sliding-window limiter. One API instance only (known weak point)."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque] = defaultdict(deque)

    def _trim(self, key: str, now: float) -> deque:
        q = self._hits[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        if not q:
            self._hits.pop(key, None)
            q = self._hits[key]
        return q

    def retry_after(self, key: str) -> int | None:
        """Seconds to wait if `key` is over the limit right now, else None. Does not count a hit."""
        now = time.monotonic()
        q = self._trim(key, now)
        if len(q) >= self.limit:
            return max(1, int(q[0] + self.window - now) + 1)
        return None

    def hit(self, key: str) -> int | None:
        """Count one hit. Returns retry-after seconds if this hit is over the limit (and is not counted)."""
        now = time.monotonic()
        q = self._trim(key, now)
        if len(q) >= self.limit:
            return max(1, int(q[0] + self.window - now) + 1)
        q.append(now)
        return None
