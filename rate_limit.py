"""
Per-IP rate limiting, the shared Godfat fetch cap, and the /optimize concurrency cap.

Render's free tier wipes local files whenever the service spins down, so counters live
in Render's Key Value store (Redis-compatible) when REDIS_URL is set. Without it (local
dev), or whenever Redis errors, an in-process MemoryStore is used instead: requests are
never refused just because Redis is down, and counting carries on, less durably.

Client IP: Render's proxy sits in front of the app, so the connection's address is the
proxy's. ClientIPMiddleware replaces it with the X-Forwarded-For entry that Render's
proxy appended -- counted from the RIGHT, one entry per trusted proxy hop. The leftmost
entry is never trusted: a client can put anything there. (uvicorn's own
--forwarded-allow-ips="*" returns exactly that leftmost entry, which is why it isn't
used.) After deploying, check this with /whoami -- see ENABLE_WHOAMI in server.py.

Environment:
    REDIS_URL             Render Key Value internal URL. Unset: in-memory counters.
    TRUSTED_PROXY_HOPS    Proxies in front of the app. Defaults to 1 on Render (which
                          sets RENDER=true), else 0 (local: use the connection address).
"""

import ipaddress
import logging
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from data_download import MAX_FETCHES_PER_HOUR, RATE_WINDOW, RateLimitError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rule:
    limit: int
    window: int  # seconds, fixed window

    @property
    def name(self):
        return f"{self.limit}per{self.window}s"


# Per-IP limits, by route group. See server.py for which route is in which group.
# "/" and /icons are deliberately not limited: static files the browser caches.
GROUPS = {
    "low": [Rule(150, 5 * 60)],                      # cheap reads of committed data
    "tracks": [Rule(5, 15 * 60)],                    # each may hit bc.godfat.org
    "optimize": [Rule(20, 15 * 60), Rule(5, 60)],    # CPU-heavy; second rule caps bursts
}

# Real bc.godfat.org requests across ALL users, over a rolling hour. The number is
# data_download's, so it stays in step with the promise in its USER_AGENT.
GODFAT_GLOBAL_LIMIT = MAX_FETCHES_PER_HOUR
GODFAT_GLOBAL_WINDOW = RATE_WINDOW

# /optimize runs at once on this instance. Render free is ~0.1 CPU, so parallel runs
# just slow each other down; past this, callers get a 503 "busy" instead.
MAX_CONCURRENT_OPTIMIZE = int(os.environ.get("MAX_CONCURRENT_OPTIMIZE", "2"))
OPTIMIZE_BUSY_RETRY_AFTER = 10  # seconds


# ---- Stores ---------------------------------------------------------------------

class MemoryStore:
    """In-process counters. Lost on restart; fine for local dev and as a fallback."""

    PRUNE_AT = 10_000  # entries before expired ones are swept

    def __init__(self):
        self._lock = threading.Lock()
        self._counters = {}  # key -> (count, expires_at)
        self._rolling = {}   # key -> list of timestamps

    def incr(self, key, ttl, now):
        with self._lock:
            if len(self._counters) > self.PRUNE_AT:
                self._counters = {k: v for k, v in self._counters.items() if v[1] > now}
            count, expires_at = self._counters.get(key, (0, now + ttl))
            if expires_at <= now:
                count, expires_at = 0, now + ttl
            self._counters[key] = (count + 1, expires_at)
            return count + 1

    def add_if_under(self, key, limit, window, now):
        with self._lock:
            times = [t for t in self._rolling.get(key, []) if now - t < window]
            admitted = len(times) < limit
            if admitted:
                times.append(now)
            self._rolling[key] = times
            return admitted, (times[0] + window - now if times else 0)


class RedisStore:
    """Counters in Redis. Any Redis error falls back to an in-process MemoryStore for
    that call, so an outage degrades to per-instance limiting instead of refusing."""

    def __init__(self, client):
        self.client = client
        self.fallback = MemoryStore()

    def incr(self, key, ttl, now):
        try:
            pipe = self.client.pipeline(transaction=True)
            pipe.incr(key)
            pipe.expire(key, ttl)  # keys are per-window, so refreshing the TTL is harmless
            return int(pipe.execute()[0])
        except Exception:
            log.exception("Redis incr failed; using in-memory fallback")
            return self.fallback.incr(key, ttl, now)

    def add_if_under(self, key, limit, window, now):
        # Add first, then count, then take it back if over: concurrent callers can at
        # worst both be refused, never both admitted past the limit.
        member = f"{now}:{uuid.uuid4().hex}"
        try:
            pipe = self.client.pipeline(transaction=True)
            pipe.zremrangebyscore(key, 0, now - window)
            pipe.zadd(key, {member: now})
            pipe.zcard(key)
            pipe.expire(key, window)
            count = pipe.execute()[2]
            if count <= limit:
                return True, 0
            self.client.zrem(key, member)
            oldest = self.client.zrange(key, 0, 0, withscores=True)
            return False, (oldest[0][1] + window - now if oldest else window)
        except Exception:
            log.exception("Redis rolling count failed; using in-memory fallback")
            return self.fallback.add_if_under(key, limit, window, now)


def make_store():
    url = os.environ.get("REDIS_URL")
    if not url:
        log.warning("REDIS_URL not set; rate-limit counters are in-memory (lost on restart)")
        return MemoryStore()
    import redis  # only needed when REDIS_URL is set
    return RedisStore(redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2))


store = make_store()  # module-level so tests can swap it


# ---- Client IP ------------------------------------------------------------------

def default_trusted_hops():
    if "TRUSTED_PROXY_HOPS" in os.environ:
        return int(os.environ["TRUSTED_PROXY_HOPS"])
    return 1 if os.environ.get("RENDER") else 0


class ClientIPMiddleware:
    """Set scope["client"] to the X-Forwarded-For entry added by the nearest trusted
    proxy: the `trusted_hops`-th entry from the right. With 0 hops, or a header with
    fewer entries than hops (not via the proxy), the connection address is kept."""

    def __init__(self, app, trusted_hops=None):
        self.app = app
        self.trusted_hops = default_trusted_hops() if trusted_hops is None else trusted_hops

    async def __call__(self, scope, receive, send):
        if self.trusted_hops and scope["type"] in ("http", "websocket"):
            values = [v.decode("latin-1") for k, v in scope["headers"] if k == b"x-forwarded-for"]
            entries = [e.strip() for e in ",".join(values).split(",") if e.strip()]
            if len(entries) >= self.trusted_hops:
                scope = dict(scope, client=(entries[-self.trusted_hops], 0))
        await self.app(scope, receive, send)


def client_key(request):
    """The rate-limit identity for a request: its IP, except IPv6 is grouped by /64,
    since one subscriber is usually handed a whole /64 to rotate through."""
    host = request.client.host if request.client else "unknown"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


# ---- FastAPI hooks --------------------------------------------------------------

def too_many(detail, retry_after):
    seconds = max(1, math.ceil(retry_after))
    return HTTPException(status_code=429, detail=f"{detail} Try again in about {wait_text(seconds)}.",
                         headers={"Retry-After": str(seconds)})


def wait_text(seconds):
    return f"{seconds} seconds" if seconds < 60 else f"{math.ceil(seconds / 60)} minute(s)"


def rate_limited(group):
    """A route dependency enforcing GROUPS[group] for the caller's IP."""
    def check(request: Request):
        ip, now = client_key(request), time.time()
        retry_after = 0
        for rule in GROUPS[group]:  # count every rule, so each window sees every request
            window_index = int(now // rule.window)
            key = f"rl:{group}:{rule.name}:{ip}:{window_index}"
            if store.incr(key, rule.window, now) > rule.limit:
                retry_after = max(retry_after, (window_index + 1) * rule.window - now)
        if retry_after:
            raise too_many("Too many requests.", retry_after)

    return Depends(check)


def godfat_fetch_gate(url, now, enforce=True):
    """Replacement for data_download.check_and_record_fetch on the server: the same
    shared cap, but counted in the store so it survives spin-downs."""
    admitted, retry_after = store.add_if_under("godfat:fetches", GODFAT_GLOBAL_LIMIT, GODFAT_GLOBAL_WINDOW, now)
    if not admitted and enforce:
        raise RateLimitError(
            f"The site-wide limit on bc.godfat.org fetches ({GODFAT_GLOBAL_LIMIT} per hour, shared by "
            f"all users, to be gentle on that site) is reached. Try again in about "
            f"{wait_text(max(1, math.ceil(retry_after)))}."
        )


_optimize_slots = threading.BoundedSemaphore(MAX_CONCURRENT_OPTIMIZE)


def optimize_slot():
    """A route dependency holding one of MAX_CONCURRENT_OPTIMIZE slots for the
    request's duration; 503 if none is free."""
    if not _optimize_slots.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="The optimizer is busy with other requests. Try again shortly.",
                            headers={"Retry-After": str(OPTIMIZE_BUSY_RETRY_AFTER)})
    try:
        yield
    finally:
        _optimize_slots.release()
