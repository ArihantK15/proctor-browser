import os
import sys
import logging
from supabase import create_client, Client

_log = logging.getLogger(__name__)


def _required_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(
            f"[boot] FATAL: env var {name} is required.\n"
            "  Local dev: add to .env at repo root.\n"
            "  Docker: set in docker-compose.yml `env_file:` or `environment:`.\n"
            "  Prod: set in DigitalOcean droplet's /etc/procta/secrets.env.",
            file=sys.stderr,
        )
        sys.exit(1)
    return v


_DATABASE_BACKEND = os.environ.get("DATABASE_BACKEND", "supabase").strip().lower()


def database_backend() -> str:
    """Single source of truth for which DB backend is active.

    Cached at module import time — callers (routers, services) should
    import this function rather than re-reading os.environ each call.
    Both for tidiness and so a future test can monkeypatch the module
    constant without env juggling.
    """
    return _DATABASE_BACKEND


def is_postgres_backend() -> bool:
    """Sugar around `database_backend() == "postgres"` for the common
    case of "should I take the postgres-only path?"."""
    return _DATABASE_BACKEND == "postgres"


class _UnavailableSupabase:
    def __getattr__(self, name):
        raise RuntimeError(
            "Supabase client is unavailable when DATABASE_BACKEND=postgres. "
            "Use local auth/Postgres adapters or keep DATABASE_BACKEND=supabase."
        )


if _DATABASE_BACKEND == "postgres":
    supabase = _UnavailableSupabase()
else:
    supabase: Client = create_client(
        _required_env("SUPABASE_URL"),
        _required_env("SUPABASE_SERVICE_ROLE_KEY"),
    )

# ─── Async Supabase client (httpx) for hot-path endpoints ────────
import httpx

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "") if _DATABASE_BACKEND == "postgres" else _required_env("SUPABASE_URL")
_SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") if _DATABASE_BACKEND == "postgres" else _required_env("SUPABASE_SERVICE_ROLE_KEY")
_REST_BASE = f"{_SUPABASE_URL}/rest/v1"
_HEADERS = {
    "apikey": _SUPABASE_KEY,
    "Authorization": f"Bearer {_SUPABASE_KEY}",
    "Content-Type": "application/json",
}

_async_client: httpx.AsyncClient | None = None
_async_client_lock = None  # initialized lazily to avoid import-time event loop issues


def _get_async_client() -> httpx.AsyncClient:
    """Get or create the shared async httpx client (thread-safe for asyncio)."""
    global _async_client
    if _async_client is None:
        # httpx.AsyncClient() constructor is synchronous — no race in single-threaded asyncio
        _async_client = httpx.AsyncClient(
            base_url=_REST_BASE,
            headers=_HEADERS,
            timeout=15.0,
        )
    return _async_client


def _pg_val(val) -> str:
    """Convert a Python value to PostgREST query parameter format."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val)


class AsyncTable:
    """Thin async wrapper around Supabase PostgREST for hot-path queries.

    Mirrors the sync supabase-py chaining API so callers look identical:
        await _atable("answers").eq("session_key", sid).upsert(row).execute()
    """

    def __init__(self, table: str):
        self._table = table
        self._filters: list[tuple[str, str, str]] = []
        self._select_cols = "*"
        self._order_col: str | None = None
        self._count_mode: str | None = None
        self._limit_val: int | None = None
        self._offset_val: int | None = None
        self._on_conflict: str | None = None
        # Mutation state (set by insert/upsert/update/delete)
        self._op: str | None = None  # "select"|"insert"|"upsert"|"update"|"delete"
        self._payload = None
        self._single = False

    def select(self, cols: str = "*", *, count: str | None = None) -> "AsyncTable":
        self._select_cols = cols
        self._count_mode = count
        self._op = "select"
        return self

    def eq(self, col: str, val) -> "AsyncTable":
        self._filters.append((col, "eq", _pg_val(val)))
        return self

    def neq(self, col: str, val) -> "AsyncTable":
        self._filters.append((col, "neq", _pg_val(val)))
        return self

    def is_(self, col: str, val) -> "AsyncTable":
        """IS NULL / IS NOT NULL check."""
        self._filters.append((col, "is", "null" if val is None else _pg_val(val)))
        return self

    def in_(self, col: str, values) -> "AsyncTable":
        """IN (val1, val2, ...) filter."""
        if isinstance(values, str):
            val_str = values
        else:
            val_str = ",".join(_pg_val(v) for v in values)
        self._filters.append((col, "in", f"({val_str})"))
        return self

    def gte(self, col: str, val) -> "AsyncTable":
        self._filters.append((col, "gte", _pg_val(val)))
        return self

    def lte(self, col: str, val) -> "AsyncTable":
        self._filters.append((col, "lte", _pg_val(val)))
        return self

    def gt(self, col: str, val) -> "AsyncTable":
        self._filters.append((col, "gt", _pg_val(val)))
        return self

    def lt(self, col: str, val) -> "AsyncTable":
        self._filters.append((col, "lt", _pg_val(val)))
        return self

    def like(self, col: str, pattern: str) -> "AsyncTable":
        self._filters.append((col, "like", pattern))
        return self

    def order(self, col: str, *, desc: bool = False) -> "AsyncTable":
        self._order_col = f"{col}.desc" if desc else col
        return self

    def limit(self, n: int) -> "AsyncTable":
        self._limit_val = n
        return self

    def range(self, start: int, end: int) -> "AsyncTable":
        """Inclusive range: rows start..end."""
        self._offset_val = start
        self._limit_val = end - start + 1
        return self

    def single(self) -> "AsyncTable":
        self._single = True
        return self

    def insert(self, rows) -> "AsyncTable":
        self._op = "insert"
        self._payload = rows if isinstance(rows, list) else [rows]
        return self

    def upsert(self, rows, on_conflict: str | None = None) -> "AsyncTable":
        self._op = "upsert"
        self._payload = rows if isinstance(rows, list) else [rows]
        self._on_conflict = on_conflict
        return self

    def update(self, fields: dict) -> "AsyncTable":
        self._op = "update"
        self._payload = fields
        return self

    def delete(self) -> "AsyncTable":
        self._op = "delete"
        return self

    def _build_params(self, include_select: bool = True) -> dict:
        params: dict = {}
        if include_select:
            params["select"] = self._select_cols
        for col, op, val in self._filters:
            params[col] = f"{op}.{val}"
        if self._order_col:
            params["order"] = self._order_col
        if self._limit_val is not None:
            params["limit"] = str(self._limit_val)
        if self._offset_val is not None:
            params["offset"] = str(self._offset_val)
        return params

    async def execute(self) -> "_AsyncResult":
        """Execute the built query against Supabase REST API."""
        c = _get_async_client()
        op = self._op or "select"

        if op == "select":
            headers = {}
            if self._single:
                headers["Prefer"] = "single-row"
            elif self._count_mode:
                headers["Prefer"] = f"count={self._count_mode}"
            resp = await c.get(f"/{self._table}",
                               params=self._build_params(), headers=headers)
            resp.raise_for_status()
            count = None
            if self._count_mode and "content-range" in resp.headers:
                try:
                    count = int(resp.headers["content-range"].split("/")[-1])
                except (ValueError, IndexError) as _pe:
                    _log.warning("Failed to parse content-range '%s': %s", resp.headers.get("content-range"), _pe)
            if self._single:
                data = resp.json() if resp.content else None
            else:
                data = resp.json() if resp.content else []
            return _AsyncResult(data=data, count=count)

        elif op == "insert":
            resp = await c.post(
                f"/{self._table}", json=self._payload,
                headers={"Prefer": "return=representation"})
            resp.raise_for_status()
            return _AsyncResult(data=resp.json())

        elif op == "upsert":
            params = {}
            if self._on_conflict:
                params["on_conflict"] = self._on_conflict
            resp = await c.post(
                f"/{self._table}", json=self._payload, params=params,
                headers={"Prefer": "resolution=merge-duplicates,return=representation"})
            resp.raise_for_status()
            return _AsyncResult(data=resp.json())

        elif op == "update":
            if not self._filters:
                raise ValueError("update() requires at least one filter to prevent updating all rows")
            resp = await c.patch(
                f"/{self._table}", params=self._build_params(include_select=False),
                json=self._payload,
                headers={"Prefer": "return=representation"})
            resp.raise_for_status()
            return _AsyncResult(data=resp.json())

        elif op == "delete":
            if not self._filters:
                raise ValueError("delete() requires at least one filter to prevent deleting all rows")
            resp = await c.delete(
                f"/{self._table}", params=self._build_params(include_select=False))
            resp.raise_for_status()
            return _AsyncResult(data=resp.json() if resp.content else [])

        else:
            raise ValueError(f"Unknown operation: {op}")


class _AsyncResult:
    def __init__(self, data=None, count=None):
        self.data = data if data is not None else []
        self.count = count


def async_table(name: str) -> AsyncTable:
    """Create an async query builder for the given table."""
    if _DATABASE_BACKEND == "postgres":
        from .postgres_table import postgres_table
        return postgres_table(name)
    return AsyncTable(name)
