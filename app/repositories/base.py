"""Typed repository base class for Supabase table access.

Replaces the ad-hoc ``_atable("table_name")`` pattern with a class-per-table
approach. Each table gets a repository that knows its column names and
provides typed query builders.

Usage::

    class StudentsRepo(Repository["students"]):
        table = "students"

    repo = StudentsRepo()
    rows = await repo.select("*").eq("roll_number", "ALICE001").execute()
"""

from __future__ import annotations
from typing import Any, Generic, TypeVar, Optional
from dataclasses import dataclass

from ..database import async_table as _atable


T = TypeVar("T", bound=str)


@dataclass
class QueryResult:
    """Typed wrapper around the raw Supabase query result."""
    data: list[dict[str, Any]]
    count: Optional[int] = None
    error: Optional[str] = None

    @property
    def first(self) -> Optional[dict[str, Any]]:
        return self.data[0] if self.data else None

    @property
    def empty(self) -> bool:
        return len(self.data) == 0

    def __bool__(self) -> bool:
        return not self.empty

    def __len__(self) -> int:
        return len(self.data)


class QueryBuilder:
    """Fluent query builder wrapping the Supabase async table interface."""

    def __init__(self, table_name: str, operation: str = "select", columns: str = "*"):
        self._table = _atable(table_name)
        self._op = operation
        self._cols = columns
        self._filters: list[tuple[str, str, Any]] = []  # (col, op, value)
        self._order_col: Optional[str] = None
        self._order_desc: bool = False
        self._limit_val: Optional[int] = None
        self._offset_val: Optional[int] = None
        self._single: bool = False
        self._data: Optional[dict] = None  # for insert/update

    def eq(self, column: str, value: Any) -> QueryBuilder:
        self._filters.append((column, "eq", value))
        return self

    def neq(self, column: str, value: Any) -> QueryBuilder:
        self._filters.append((column, "neq", value))
        return self

    def gt(self, column: str, value: Any) -> QueryBuilder:
        self._filters.append((column, "gt", value))
        return self

    def gte(self, column: str, value: Any) -> QueryBuilder:
        self._filters.append((column, "gte", value))
        return self

    def lt(self, column: str, value: Any) -> QueryBuilder:
        self._filters.append((column, "lt", value))
        return self

    def lte(self, column: str, value: Any) -> QueryBuilder:
        self._filters.append((column, "lte", value))
        return self

    def in_(self, column: str, values: list[Any]) -> QueryBuilder:
        self._filters.append((column, "in", values))
        return self

    def order(self, column: str, desc: bool = False) -> QueryBuilder:
        self._order_col = column
        self._order_desc = desc
        return self

    def limit(self, n: int) -> QueryBuilder:
        self._limit_val = n
        return self

    def offset(self, n: int) -> QueryBuilder:
        self._offset_val = n
        return self

    def maybe_single(self) -> QueryBuilder:
        self._single = True
        return self

    async def execute(self) -> QueryResult:
        """Execute the query and return a typed result."""
        q = self._table
        if self._op == "select":
            q = q.select(self._cols)
        elif self._op == "insert":
            q = q.insert(self._data)
        elif self._op == "update":
            q = q.update(self._data)
        elif self._op == "delete":
            q = q.delete()

        for col, op, val in self._filters:
            if op == "eq":
                q = q.eq(col, val)
            elif op == "neq":
                q = q.neq(col, val)
            elif op == "gt":
                q = q.gt(col, val)
            elif op == "gte":
                q = q.gte(col, val)
            elif op == "lt":
                q = q.lt(col, val)
            elif op == "lte":
                q = q.lte(col, val)
            elif op == "in":
                q = q.in_(col, val)

        if self._order_col:
            q = q.order(self._order_col, desc=self._order_desc)
        if self._limit_val is not None:
            q = q.limit(self._limit_val)
        if self._offset_val is not None:
            q = q.offset(self._offset_val)
        if self._single:
            q = q.maybe_single()

        raw = await q.execute()
        return QueryResult(
            data=raw.data or [],
            count=getattr(raw, "count", None),
            error=getattr(raw, "error", None) if hasattr(raw, "error") else None,
        )


class Repository(Generic[T]):
    """Base class for table-specific repositories.

    Override ``table`` to set the table name. Provides select/insert/update/delete
    builders that chain filters fluently.
    """

    table: str

    def select(self, columns: str = "*") -> QueryBuilder:
        return QueryBuilder(self.table, "select", columns)

    def insert(self, data: dict) -> QueryBuilder:
        b = QueryBuilder(self.table, "insert")
        b._data = data
        return b

    def update(self, data: dict) -> QueryBuilder:
        b = QueryBuilder(self.table, "update")
        b._data = data
        return b

    def delete(self) -> QueryBuilder:
        return QueryBuilder(self.table, "delete")

    async def get(self, **filters: Any) -> QueryResult:
        """Shortcut for select + eq chain + execute."""
        q = self.select()
        for col, val in filters.items():
            q = q.eq(col, val)
        return await q.execute()

    async def get_one(self, **filters: Any) -> Optional[dict[str, Any]]:
        """Shortcut that returns the first row or None."""
        result = await self.get(**filters)
        return result.first

    async def create(self, data: dict) -> QueryResult:
        b = self.insert(data)
        return await b.execute()

    async def upsert(self, data: dict, on_conflict: str = "id") -> QueryResult:
        """Insert or update on conflict. Uses Supabase's upsert via raw query."""
        q = _atable(self.table)
        if on_conflict:
            q = q.upsert(data, on_conflict=on_conflict)
        else:
            q = q.upsert(data)
        raw = await q.execute()
        return QueryResult(data=raw.data or [])
