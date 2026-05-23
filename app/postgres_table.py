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

# Per-table default ON CONFLICT columns for upserts.
# Supabase REST infers the conflict target from the table's primary key
# or unique index automatically; asyncpg requires us to spell it out.
# Most tables use `id`, but several core tables key off something else.
# Callers can still override via `.upsert(rows, on_conflict='col,...')`.
_UPSERT_CONFLICT_COLS: dict[str, str] = {
    "exam_sessions":           "session_key",
    "answers":                 "session_key,question_id",
    "questions":               "teacher_id,exam_id,question_id",
    "exam_config":             "teacher_id,exam_id",
    "student_invites":         "id",
    "student_group_members":   "group_id,student_id",
    "exam_group_assignments":  "exam_id,group_id",
    "grading_audit":           "id",
}

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
        # Tuned defaults for production exam load (2026-05-22):
        #
        # min_size=20 keeps 20 warm connections so the first burst of
        # the join wave (3000 students hitting exam_started within
        # 120s) doesn't pay TCP+TLS+auth handshake latency. At 4
        # uvicorn workers that's 80 connections held warm — well under
        # postgres max_connections=200, leaves headroom for the worker
        # containers + autosave.
        #
        # max_size=40 caps each uvicorn worker's pool. 4 workers × 40 =
        # 160 max, again under max_connections=200.
        # When DATABASE_URL points at pgbouncer (transaction pooling),
        # asyncpg needs TWO compatibility settings — `statement_cache_size=0`
        # ALONE is not enough:
        #
        #   1. statement_cache_size=0
        #        Disables asyncpg's in-process REUSE of prepared
        #        statements. Without this, asyncpg looks up cached
        #        statement handles bound to a specific backend connection,
        #        which under transaction-pool reassignment fails.
        #
        #   2. prepared_statement_name_func=<uuid-generator>
        #        asyncpg STILL uses prepared statements at the wire-
        #        protocol level (the `PREPARE name AS ...` then
        #        `EXECUTE name (...)` dance) even with the cache off.
        #        Default naming is a per-connection counter
        #        (__asyncpg_stmt_1__, __asyncpg_stmt_2__, ...).
        #        Under transaction pooling, when pgbouncer reuses a
        #        backend that already has a `__asyncpg_stmt_1__`
        #        registered from a previous client, the new PREPARE
        #        for that name fails with:
        #           "prepared statement '__asyncpg_stmt_1__' already exists"
        #        We diagnosed exactly this error at 3000 VU on
        #        2026-05-23, causing 79/3000 submit failures (2.6%).
        #        Generating a UUID-based name per query makes
        #        collisions cryptographically impossible.
        #
        # Outside pgbouncer mode we keep the default counter naming
        # AND the statement cache — both are wins when connecting
        # direct to Postgres.
        pgbouncer_mode = os.environ.get("DATABASE_USE_PGBOUNCER", "").lower() in ("1", "true", "yes")

        pool_kwargs: dict = dict(
            dsn=_database_url(),
            min_size=int(os.environ.get("POSTGRES_POOL_MIN", "20")),
            max_size=int(os.environ.get("POSTGRES_POOL_MAX", "40")),
            command_timeout=float(os.environ.get("POSTGRES_COMMAND_TIMEOUT", "15")),
            max_inactive_connection_lifetime=float(os.environ.get("POSTGRES_IDLE_LIFETIME", "60")),
            timeout=float(os.environ.get("POSTGRES_CONNECT_TIMEOUT", "10")),
        )
        if pgbouncer_mode:
            import uuid
            pool_kwargs["statement_cache_size"] = 0
            # Each prepared statement gets a fresh UUID-based name, so
            # backend reuse via pgbouncer cannot produce name collisions.
            # The 16-hex-char suffix gives 64 bits of entropy — collision
            # probability is essentially zero even at millions of QPS.
            pool_kwargs["prepared_statement_name_func"] = (
                lambda: f"__procta_{uuid.uuid4().hex[:16]}__"
            )
        else:
            pool_kwargs["statement_cache_size"] = 100  # asyncpg default

        _pool = await asyncpg.create_pool(**pool_kwargs)
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
        # asyncpg's prepared-statement parameter binding rejects ISO 8601
        # strings as values for timestamptz columns — it requires a real
        # datetime instance. The legacy Supabase REST adapter tolerated
        # strings (JSON serialization), and the codebase has dozens of
        # `now_ist().isoformat()` and `dt.isoformat()` calls that pass
        # strings through this layer. Coerce here so all of them keep
        # working on the postgres backend without per-callsite churn.
        #
        # Heuristic: only attempt coercion on strings of plausible ISO
        # length that contain 'T' — guarantees we don't try to coerce
        # short identifiers, emails, UA strings, or other free text.
        # If `fromisoformat()` raises, we leave the value as a string.
        if isinstance(value, str) and 16 <= len(value) <= 40 and "T" in value:
            try:
                from datetime import datetime as _dt
                # fromisoformat in 3.11+ accepts trailing 'Z' as +00:00;
                # the .replace() makes older Pythons accept it too.
                value = _dt.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass  # not an ISO timestamp — leave as-is
        self.params.append(value)
        return f"${len(self.params)}"


class _NotFilter:
    """Small PostgREST `.not_` proxy used by Supabase-shaped callers."""

    def __init__(self, table: "PostgresTable"):
        self._table = table

    def _add(self, col: str, op: str, val) -> "PostgresTable":
        self._table._filters.append((col, f"not:{op}", val))
        return self._table

    def is_(self, col: str, val) -> "PostgresTable":
        return self._add(col, "is", val)

    def eq(self, col: str, val) -> "PostgresTable":
        return self._add(col, "=", val)

    def neq(self, col: str, val) -> "PostgresTable":
        return self._add(col, "!=", val)

    def in_(self, col: str, values) -> "PostgresTable":
        return self._add(col, "in", list(values))

    def like(self, col: str, pattern: str) -> "PostgresTable":
        return self._add(col, "like", pattern)

    def ilike(self, col: str, pattern: str) -> "PostgresTable":
        return self._add(col, "ilike", pattern)

    def gt(self, col: str, val) -> "PostgresTable":
        return self._add(col, ">", val)

    def gte(self, col: str, val) -> "PostgresTable":
        return self._add(col, ">=", val)

    def lt(self, col: str, val) -> "PostgresTable":
        return self._add(col, "<", val)

    def lte(self, col: str, val) -> "PostgresTable":
        return self._add(col, "<=", val)


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

    @property
    def not_(self) -> _NotFilter:
        return _NotFilter(self)

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
            elif op == "not:is":
                clauses.append(f"{col_sql} IS {'NOT NULL' if val in (None, 'null') else 'NULL'}")
            elif op == "in":
                clauses.append(f"{col_sql} = ANY({sql.add(val)})")
            elif op == "not:in":
                clauses.append(f"{col_sql} <> ALL({sql.add(val)})")
            elif op == "like":
                clauses.append(f"{col_sql} LIKE {sql.add(val)}")
            elif op == "not:like":
                clauses.append(f"{col_sql} NOT LIKE {sql.add(val)}")
            elif op == "ilike":
                clauses.append(f"{col_sql} ILIKE {sql.add(val)}")
            elif op == "not:ilike":
                clauses.append(f"{col_sql} NOT ILIKE {sql.add(val)}")
            elif op.startswith("not:"):
                clauses.append(f"NOT ({col_sql} {op.removeprefix('not:')} {sql.add(val)})")
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
                # Resolve conflict column: explicit override wins, otherwise
                # use the per-table default registry, otherwise fall back to
                # "id" (the most common PK).
                default_conflict = _UPSERT_CONFLICT_COLS.get(self._table, "id")
                conflict_cols = [c.strip() for c in (self._on_conflict or default_conflict).split(",") if c.strip()]
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
