"""
Security layer for the Finance Analyser web API.

Two responsibilities:

1. validate_read_only_sql() — a strict allow-list validator for the SQL that
   Claude generates from a user's natural-language question. Because the SQL is
   executed against DuckDB, an attacker who can influence the question (e.g. via
   prompt injection) could otherwise try to read server files, reach the network,
   load extensions, or mutate data. This validator is the first of two layers;
   the second is a locked-down read-only DuckDB connection (see database.py).

2. RateLimiter — a dependency-free, in-memory sliding-window limiter plus a hard
   global daily ceiling on the endpoints that call the paid Claude API. On a
   single Render instance this is sufficient to stop both per-client abuse and
   runaway API spend.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque

from fastapi import HTTPException, Request


# ============================================================================
#  SQL VALIDATION
# ============================================================================

# Whole-word tokens that must never appear in a user-driven query. Word
# boundaries (\b) mean "OFFSET" will not trip "SET", "RESET" will not trip
# "SET", and the legitimate scalar replace()/list functions are unaffected
# because their dangerous DDL forms (CREATE/INSERT) are blocked directly.
_FORBIDDEN_TOKENS = [
    # DML / DDL — anything that writes or changes structure
    "insert", "update", "delete", "drop", "create", "alter", "truncate",
    "merge", "upsert", "vacuum", "checkpoint", "reindex", "analyze",
    # Session / catalog control
    "attach", "detach", "set", "reset", "pragma", "call", "use",
    "install", "load", "export", "import", "copy",
    # File / network / extension scan functions (read_csv, read_text,
    # read_parquet, read_json, read_blob, parquet_scan, delta_scan, …)
    "glob", "sniff_csv",
    # System / introspection that can leak the host
    "getenv", "which_secret", "shell",
]

# Function-style sinks matched as a prefix family.
_FORBIDDEN_PATTERNS = [
    r"\bread_[a-z0-9_]*\b",     # read_csv, read_csv_auto, read_text, read_blob, read_json, read_parquet
    r"\b[a-z0-9_]*_scan\b",     # parquet_scan, delta_scan, iceberg_scan, read_parquet_scan
    r"\bgenerate_series\b",     # cheap row-bomb vector — block to be safe
]

_TOKEN_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in _FORBIDDEN_TOKENS) + r")\b",
    re.IGNORECASE,
)
_PATTERN_RES = [re.compile(p, re.IGNORECASE) for p in _FORBIDDEN_PATTERNS]

# Maximum characters in a generated query — a sane upper bound that still allows
# multi-CTE analytical queries while refusing pathological payloads.
_MAX_SQL_LEN = 4000


class UnsafeQueryError(ValueError):
    """Raised when generated SQL fails the read-only safety check."""


def validate_read_only_sql(sql: str) -> str:
    """
    Return the cleaned SQL if it is a single read-only statement, else raise
    UnsafeQueryError. This is intentionally strict: it is better to refuse a
    rare legitimate query than to permit a dangerous one.
    """
    if not sql or not sql.strip():
        raise UnsafeQueryError("Empty query.")

    cleaned = sql.strip()

    if len(cleaned) > _MAX_SQL_LEN:
        raise UnsafeQueryError("Query is too long.")

    # Reject SQL comments outright — they are never needed here and are a common
    # way to smuggle or obfuscate payloads.
    if "--" in cleaned or "/*" in cleaned or "*/" in cleaned:
        raise UnsafeQueryError("Comments are not allowed in queries.")

    # Collapse a single trailing semicolon, then forbid any remaining one: that
    # guarantees exactly one statement (no stacked `; DROP TABLE …`).
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if ";" in cleaned:
        raise UnsafeQueryError("Multiple statements are not allowed.")

    # Must be a pure read: SELECT or a WITH … SELECT CTE.
    lowered = cleaned.lstrip("( \t\r\n").lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise UnsafeQueryError("Only SELECT queries are permitted.")

    # Whole-word denylist.
    hit = _TOKEN_RE.search(cleaned)
    if hit:
        raise UnsafeQueryError(f"Disallowed keyword: {hit.group(1).upper()}")

    for rx in _PATTERN_RES:
        m = rx.search(cleaned)
        if m:
            raise UnsafeQueryError(f"Disallowed function: {m.group(0)}")

    return cleaned


# ============================================================================
#  RATE LIMITING  (in-memory, single-instance)
# ============================================================================

def client_ip(request: Request) -> str:
    """
    Best-effort real client IP. Render terminates TLS at a proxy, so the
    originating address is the first hop in X-Forwarded-For. Fall back to the
    socket peer when the header is absent.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class _SlidingWindow:
    """Per-key sliding-window counter with bounded memory."""

    def __init__(self, max_keys: int = 10_000):
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._max_keys = max_keys

    def allow(self, key: str, limit: int, window_s: float) -> bool:
        now = time.monotonic()
        cutoff = now - window_s
        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                # Evict if the table has grown too large (cheapest possible GC).
                if len(self._hits) >= self._max_keys:
                    self._hits.clear()
                dq = deque()
                self._hits[key] = dq
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= limit:
                return False
            dq.append(now)
            return True


class _DailyCeiling:
    """Process-wide hard cap that resets every 24h — bounds total API spend."""

    def __init__(self, limit: int):
        self._limit = limit
        self._count = 0
        self._reset_at = time.time() + 86_400
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = time.time()
            if now >= self._reset_at:
                self._count = 0
                self._reset_at = now + 86_400
            if self._count >= self._limit:
                return False
            self._count += 1
            return True


_window = _SlidingWindow()

# Per-IP per-minute limits, keyed by a logical endpoint name.
_PER_MINUTE = {
    "query":   20,
    "analyze": 12,   # most expensive (high effort + adaptive thinking)
    "import":  10,
    "sample":  30,
}

# Hard daily ceiling across ALL clients for endpoints that spend on Claude.
_daily_claude = _DailyCeiling(limit=600)


def enforce_rate_limit(request: Request, name: str, *, counts_against_daily: bool) -> None:
    """
    FastAPI-friendly guard. Raises HTTP 429 with Retry-After on breach.
    `name` selects the per-minute budget; `counts_against_daily` marks
    endpoints that trigger a paid Claude call.
    """
    ip = client_ip(request)
    per_min = _PER_MINUTE.get(name, 20)

    if not _window.allow(f"{name}:{ip}", per_min, 60.0):
        raise HTTPException(
            status_code=429,
            detail="Too many requests — please wait a minute and try again.",
            headers={"Retry-After": "60"},
        )

    if counts_against_daily and not _daily_claude.allow():
        raise HTTPException(
            status_code=429,
            detail="The live demo has reached its daily usage limit. Please try again tomorrow.",
            headers={"Retry-After": "3600"},
        )
