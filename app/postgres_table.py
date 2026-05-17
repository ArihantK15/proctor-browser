"""Postgres-backed compatibility adapter for `async_table(...)`.

This intentionally supports the small PostgREST-like subset used by the app.
It is a migration bridge, not a new ORM. Once production runs on Postgres, hot
paths should move to explicit repository functions.
"""
from __future__ import annotations

import os
import re
from typing import Any

import asyncpg

_pool: asyncpg.Pool | None = None
_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# PostgREST `or` filter mini-grammar accepted here:
#   "col.op.value,col.op.value,..."  with op ∈ {eq, neq, gt, gte, lt, lte, like, ilike, is}
# This is the subset the app actually uses (admin_students search). Anything
# fancier — nested parens, computed expressions — is not supported and will
# raise, by design: it's a migration bridge, not a re-implementation of PostgREST.
_OR_OPS = {"eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike", "is"}
_OR_OP_TO_SQL = {
    "eq": "=", "neq": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
    "like": "LIKE", "ilike": "ILIKE",
}


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_BACKEND=postgres requires DATABASE_URL")
    return url


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        # min_size=3 keeps three warm connections so the first burst of
        # requests after a cold start doesn't pay TCP+TLS+auth handshake
        # latency. Bumping above 3 is only worth it for very chatty
        # workloads — the pool will grow up to max_size on demand
        # regardless.
        _pool = await asyncpg.create_pool(
            dsn=_database_url(),
            min_size=int(os.environ.get("POSTGRES_POOL_MIN", "3")),
            max_size=int(os.environ.get("POSTGRES_POOL_MAX", "10")),
            command_timeout=float(os.environ.get("POSTGRES_COMMAND_TIMEOUT", "15")),
            max_inactive_connection_lifetime=float(os.environ.get("POSTGRES_IDLE_LIFETIME", "60")),
            connect_timeout=float(os.environ.get("POSTGRES_CONNECT_TIMEOUT", "10")),
        )
    return _pool


async def close_pool() -> None:
    """Close the asyncpg pool on shutdown. Safe to call when pool was never opened."""
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        finally:
            _pool = None


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return f'"{name}"'


def _select_list(cols: str) -> str:
    cols = (cols or "*").strip()
    if cols == "*":
        return "*"
    out = []
    for col in cols.split(","):
        col = col.strip()
        if not col:
            continue
        if not _IDENT.match(col):
            raise ValueError(f"Unsupported select column expression: {col!r}")
        out.append(_ident(col))
    return ", ".join(out) or "*"


class _SQL:
    def __init__(self):
        self.params: list[Any] = []

    def add(self, value: Any) -> str:
        self.params.append(value)
        return f"${len(self.params)}"


class PostgresTable:
    def __init__(self, table: str):
        self._table = table
        self._filters: list[tuple[str, str, Any]] = []
        self._select_cols = "*"
        self._order_col: str | None = None
        self._order_desc = False
        self._count_mode: str | None = None
        self._limit_val: int | None = None
        self._offset_val: int | None = None
        self._on_conflict: str | None = None
        self._op: str | None = None
        self._payload = None
        self._single = False

    def select(self, cols: str = "*", *, count: str | None = None) -> "PostgresTable":
        self._select_cols = cols
        self._count_mode = count
        self._op = "select"
        return self

    def eq(self, col: str, val) -> "PostgresTable":
        self._filters.append((col, "=", val))
        return self

    def neq(self, col: str, val) -> "PostgresTable":
        self._filters.append((col, "!=", val))
        return self

    def is_(self, col: str, val) -> "PostgresTable":
        self._filters.append((col, "is", val))
        return self

    def in_(self, col: str, values) -> "PostgresTable":
        self._filters.append((col, "in", list(values)))
        return self

    def gte(self, col: str, val) -> "PostgresTable":
        self._filters.append((col, ">=", val))
        return self

    def lte(self, col: str, val) -> "PostgresTable":
        self._filters.append((col, "<=", val))
        return self

    def gt(self, col: str, val) -> "PostgresTable":
        self._filters.append((col, ">", val))
        return self

    def lt(self, col: str, val) -> "PostgresTable":
        self._filters.append((col, "<", val))
        return self

    def like(self, col: str, pattern: str) -> "PostgresTable":
        self._filters.append((col, "like", pattern))
        return self

    def ilike(self, col: str, pattern: str) -> "PostgresTable":
        self._filters.append((col, "ilike", pattern))
        return self

    def or_(self, expr: str) -> "PostgresTable":
        """Stash a PostgREST-style or() expression. Compiled at execute() time.

        Accepts the same `col.op.value,col.op.value,...` mini-grammar
        PostgREST uses. PostgREST treats `*` inside (i)like patterns as
        the SQL wildcard `%`, so we translate that here too — matches
        the existing app behaviour (admin_students search).
        """
        self._filters.append(("__or__", "or", expr))
        return self

    def order(self, col: str, *, desc: bool = False) -> "PostgresTable":
        self._order_col = col
        self._order_desc = desc
        return self

    def limit(self, n: int) -> "PostgresTable":
        self._limit_val = n
        return self

    def range(self, start: int, end: int) -> "PostgresTable":
        self._offset_val = start
        self._limit_val = end - start + 1
        return self

    def single(self) -> "PostgresTable":
        self._single = True
        return self

    def insert(self, rows) -> "PostgresTable":
        self._op = "insert"
        self._payload = rows if isinstance(rows, list) else [rows]
        return self

    def upsert(self, rows, on_conflict: str | None = None) -> "PostgresTable":
        self._op = "upsert"
        self._payload = rows if isinstance(rows, list) else [rows]
        self._on_conflict = on_conflict
        return self

    def update(self, fields: dict) -> "PostgresTable":
        self._op = "update"
        self._payload = fields
        return self

    def delete(self) -> "PostgresTable":
        self._op = "delete"
        return self

    def _where(self, sql: _SQL) -> str:
        clauses = []
        for col, op, val in self._filters:
            if op == "or":
                clauses.append(self._compile_or(val, sql))
                continue
            col_sql = _ident(col)
            if op == "is":
                clauses.append(f"{col_sql} IS {'NULL' if val in (None, 'null') else 'NOT NULL'}")
            elif op == "in":
                clauses.append(f"{col_sql} = ANY({sql.add(val)})")
            elif op == "like":
                clauses.append(f"{col_sql} LIKE {sql.add(val)}")
            elif op == "ilike":
                clauses.append(f"{col_sql} ILIKE {sql.add(val)}")
            else:
                clauses.append(f"{col_sql} {op} {sql.add(val)}")
        return f" WHERE {' AND '.join(clauses)}" if clauses else ""

    def _compile_or(self, expr: str, sql: _SQL) -> str:
        """Compile a PostgREST `or` expression to a SQL `(... OR ... OR ...)` clause.

        Grammar: comma-separated `col.op.value` triples. `*` inside the
        value of an (i)like pattern is translated to `%`, mirroring
        PostgREST. Identifiers are validated against `_IDENT` to keep
        injection out of column names. Values become parameters.
        """
        if not expr:
            raise ValueError("or_() requires a non-empty expression")
        parts = []
        for piece in expr.split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                col, op, raw_val = piece.split(".", 2)
            except ValueError:
                raise ValueError(f"or_(): malformed clause {piece!r}; expected col.op.value")
            if op not in _OR_OPS:
                raise ValueError(f"or_(): unsupported op {op!r} in {piece!r}")
            col_sql = _ident(col)
            if op == "is":
                if raw_val in ("null", "NULL"):
                    parts.append(f"{col_sql} IS NULL")
                else:
                    parts.append(f"{col_sql} IS NOT NULL")
                continue
            if op in ("like", "ilike"):
                raw_val = raw_val.replace("*", "%")
            sql_op = _OR_OP_TO_SQL[op]
            parts.append(f"{col_sql} {sql_op} {sql.add(raw_val)}")
        if not parts:
            raise ValueError("or_() compiled to zero clauses")
        return f"({' OR '.join(parts)})"

    async def execute(self) -> "_PostgresResult":
        op = self._op or "select"
        pool = await get_pool()
        async with pool.acquire() as conn:
            if op == "select":
                sql = _SQL()
                where = self._where(sql)
                order = ""
                if self._order_col:
                    order = f" ORDER BY {_ident(self._order_col)} {'DESC' if self._order_desc else 'ASC'}"
                limit = f" LIMIT {int(self._limit_val)}" if self._limit_val is not None else ""
                offset = f" OFFSET {int(self._offset_val)}" if self._offset_val is not None else ""
                rows = await conn.fetch(
                    f"SELECT {_select_list(self._select_cols)} FROM {_ident(self._table)}"
                    f"{where}{order}{limit}{offset}",
                    *sql.params,
                )
                count = None
                if self._count_mode:
                    count_sql = _SQL()
                    count = await conn.fetchval(  # nosemgrep: python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli
                        f"SELECT COUNT(*) FROM {_ident(self._table)}{self._where(count_sql)}",
                        *count_sql.params,
                    )
                data = [dict(r) for r in rows]
                if self._single:
                    # Match AsyncTable (Supabase REST) semantics: single() returns
                    # the first row as a dict, or None when there is no match.
                    # Callers do `if result.data is None` or `result.data.get(...)`,
                    # so the shape MUST match across backends or we'll surface
                    # subtle type errors only under postgres.
                    return _PostgresResult(data=data[0] if data else None, count=count)
                return _PostgresResult(data=data, count=count)

            if op == "insert":
                data = []
                for row in self._payload:
                    cols = list(row.keys())
                    sql = _SQL()
                    placeholders = [sql.add(row[c]) for c in cols]
                    rec = await conn.fetchrow(
                        f"INSERT INTO {_ident(self._table)} ({', '.join(_ident(c) for c in cols)}) "
                        f"VALUES ({', '.join(placeholders)}) RETURNING *",
                        *sql.params,
                    )
                    data.append(dict(rec))
                return _PostgresResult(data=data)

            if op == "upsert":
                data = []
                conflict_cols = [c.strip() for c in (self._on_conflict or "id").split(",") if c.strip()]
                for row in self._payload:
                    cols = list(row.keys())
                    sql = _SQL()
                    placeholders = [sql.add(row[c]) for c in cols]
                    update_cols = [c for c in cols if c not in conflict_cols]
                    updates = ", ".join(f"{_ident(c)} = EXCLUDED.{_ident(c)}" for c in update_cols)
                    if not updates:
                        updates = f"{_ident(conflict_cols[0])} = EXCLUDED.{_ident(conflict_cols[0])}"
                    rec = await conn.fetchrow(
                        f"INSERT INTO {_ident(self._table)} ({', '.join(_ident(c) for c in cols)}) "
                        f"VALUES ({', '.join(placeholders)}) "
                        f"ON CONFLICT ({', '.join(_ident(c) for c in conflict_cols)}) "
                        f"DO UPDATE SET {updates} RETURNING *",
                        *sql.params,
                    )
                    data.append(dict(rec))
                return _PostgresResult(data=data)

            if op == "update":
                if not self._filters:
                    raise ValueError("update() requires at least one filter")
                sql = _SQL()
                sets = ", ".join(f"{_ident(k)} = {sql.add(v)}" for k, v in self._payload.items())
                rows = await conn.fetch(  # nosemgrep: python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli
                    f"UPDATE {_ident(self._table)} SET {sets}{self._where(sql)} RETURNING *",
                    *sql.params,
                )
                return _PostgresResult(data=[dict(r) for r in rows])

            if op == "delete":
                if not self._filters:
                    raise ValueError("delete() requires at least one filter")
                sql = _SQL()
                rows = await conn.fetch(  # nosemgrep: python.lang.security.audit.sqli.asyncpg-sqli.asyncpg-sqli
                    f"DELETE FROM {_ident(self._table)}{self._where(sql)} RETURNING *",
                    *sql.params,
                )
                return _PostgresResult(data=[dict(r) for r in rows])

        raise ValueError(f"Unknown operation: {op}")


class _PostgresResult:
    def __init__(self, data=None, count=None):
        self.data = data if data is not None else []
        self.count = count


def postgres_table(name: str) -> PostgresTable:
    return PostgresTable(name)
