import os
from supabase import create_client, Client

supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

# ─── Async Supabase client (httpx) for hot-path endpoints ────────
import httpx

_SUPABASE_URL = os.environ["SUPABASE_URL"]
_SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
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
        # Mutation state (set by insert/upsert/update/delete)
        self._op: str | None = None  # "insert"|"upsert"|"update"|"delete"
        self._payload = None

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

    def order(self, col: str, *, desc: bool = False) -> "AsyncTable":
        self._order_col = f"{col}.desc" if desc else col
        return self

    def insert(self, rows) -> "AsyncTable":
        self._op = "insert"
        self._payload = rows if isinstance(rows, list) else [rows]
        return self

    def upsert(self, rows) -> "AsyncTable":
        self._op = "upsert"
        self._payload = rows if isinstance(rows, list) else [rows]
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
        return params

    async def execute(self) -> "_AsyncResult":
        """Execute the built query against Supabase REST API."""
        c = _get_async_client()
        op = self._op or "select"

        if op == "select":
            headers = {}
            if self._count_mode:
                headers["Prefer"] = f"count={self._count_mode}"
            resp = await c.get(f"/{self._table}",
                               params=self._build_params(), headers=headers)
            resp.raise_for_status()
            count = None
            if self._count_mode and "content-range" in resp.headers:
                try:
                    count = int(resp.headers["content-range"].split("/")[-1])
                except (ValueError, IndexError):
                    pass
            return _AsyncResult(data=resp.json(), count=count)

        elif op == "insert":
            resp = await c.post(
                f"/{self._table}", json=self._payload,
                headers={"Prefer": "return=representation"})
            resp.raise_for_status()
            return _AsyncResult(data=resp.json())

        elif op == "upsert":
            resp = await c.post(
                f"/{self._table}", json=self._payload,
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
    return AsyncTable(name)
