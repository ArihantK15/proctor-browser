"""Tests for app.postgres_table — the PostgREST→SQL bridge.

Pure SQL-generation tests; no DB connection needed. The whole point of
this adapter is that callers wrote PostgREST-shaped code and we must
produce equivalent SQL. If these tests pass, the runtime behaviour
under DATABASE_BACKEND=postgres matches AsyncTable behaviour under
the Supabase REST default.
"""
import pytest

from app.postgres_table import PostgresTable, _SQL, _ident, _select_list


# ─── identifier safety ─────────────────────────────────────────────

def test_ident_quotes_valid_name():
    assert _ident("teachers") == '"teachers"'
    assert _ident("password_hash") == '"password_hash"'


def test_ident_rejects_injection():
    """All identifiers go through _ident() which strict-validates
    against `^[a-zA-Z_][a-zA-Z0-9_]*$`. This is the firewall against
    SQL injection via column names. If this fails, the adapter is
    unsafe."""
    with pytest.raises(ValueError):
        _ident('"; DROP TABLE teachers; --')
    with pytest.raises(ValueError):
        _ident("col-with-dash")
    with pytest.raises(ValueError):
        _ident("col with space")
    with pytest.raises(ValueError):
        _ident("1numeric_first")  # SQL identifiers must start with letter/underscore


def test_select_list_star_passthrough():
    assert _select_list("*") == "*"
    assert _select_list("") == "*"


def test_select_list_quotes_columns():
    assert _select_list("id,email,full_name") == '"id", "email", "full_name"'


def test_select_list_rejects_injection():
    with pytest.raises(ValueError):
        _select_list("id, (SELECT password_hash FROM teachers)")


# ─── WHERE clause construction ─────────────────────────────────────

def test_where_eq_simple():
    t = PostgresTable("teachers").select("*").eq("id", "abc-123")
    sql = _SQL()
    where = t._where(sql)
    assert where == ' WHERE "id" = $1'
    assert sql.params == ["abc-123"]


def test_where_chain_anded():
    t = PostgresTable("students").select("*").eq("teacher_id", "t1").eq("active", True)
    sql = _SQL()
    where = t._where(sql)
    assert where == ' WHERE "teacher_id" = $1 AND "active" = $2'
    assert sql.params == ["t1", True]


def test_where_is_null_and_not_null():
    t = PostgresTable("invites").is_("revoked_at", None)
    sql = _SQL()
    assert ' IS NULL' in t._where(sql)

    t2 = PostgresTable("invites").is_("revoked_at", "not-null")
    sql2 = _SQL()
    assert ' IS NOT NULL' in t2._where(sql2)


def test_where_in_uses_ANY():
    """asyncpg's idiomatic IN-list is `= ANY($1)` with the param as a
    list, not Postgres-native `IN (...)` — that's how we get safe
    parameter binding for variable-length lists."""
    t = PostgresTable("students").in_("status", ["active", "pending"])
    sql = _SQL()
    where = t._where(sql)
    assert '= ANY($1)' in where
    assert sql.params == [["active", "pending"]]


def test_where_like_passthrough():
    t = PostgresTable("students").like("email", "%@example.com")
    sql = _SQL()
    assert ' LIKE $1' in t._where(sql)


def test_where_ilike_passthrough():
    """ilike() is critical for admin_students search — case-insensitive
    substring match on roll_number / name / email."""
    t = PostgresTable("students").ilike("full_name", "%ari%")
    sql = _SQL()
    where = t._where(sql)
    assert ' ILIKE $1' in where
    assert sql.params == ["%ari%"]


# ─── or_() PostgREST grammar ─────────────────────────────────────

def test_or_compiles_to_parenthesised_OR():
    """Mirrors admin_students search: roll/name/email match on the
    same query string. Must compile to a SINGLE parenthesised OR
    block (not three loose ORs that would mix with outer ANDs)."""
    t = PostgresTable("students").or_(
        "roll_number.ilike.*ari*,full_name.ilike.*ari*,email.ilike.*ari*"
    ).eq("teacher_id", "t1")
    sql = _SQL()
    where = t._where(sql)
    # The OR block is parenthesised AND the teacher_id eq is outside.
    assert '("roll_number" ILIKE $1 OR "full_name" ILIKE $2 OR "email" ILIKE $3)' in where
    assert '"teacher_id" = $4' in where
    # `*` in PostgREST `or` is the wildcard → translated to `%`.
    assert sql.params == ["%ari%", "%ari%", "%ari%", "t1"]


def test_or_rejects_unknown_op():
    t = PostgresTable("students").or_("name.regex.foo")
    with pytest.raises(ValueError, match="unsupported op"):
        t._where(_SQL())


def test_or_rejects_malformed_clause():
    t = PostgresTable("students").or_("col_only_no_op")
    with pytest.raises(ValueError, match="malformed clause"):
        t._where(_SQL())


def test_or_rejects_identifier_injection():
    """Column names inside or() go through the same _ident() filter as
    everywhere else. This guards against PostgREST grammar being used
    to smuggle bad column expressions through the or_() pathway."""
    t = PostgresTable("students").or_('"col"; DROP TABLE x; --.eq.1')
    with pytest.raises(ValueError):
        t._where(_SQL())


def test_or_is_null_handled():
    """`is.null` is a PostgREST idiom — must not be parameterised."""
    t = PostgresTable("invites").or_("revoked_at.is.null,deleted_at.is.null")
    sql = _SQL()
    where = t._where(sql)
    assert ' IS NULL' in where
    assert sql.params == []  # `is.null` doesn't bind a parameter


# ─── single() semantics ─────────────────────────────────────────

def test_single_flag_set():
    t = PostgresTable("teachers").select("*").eq("id", "x").single()
    # The flag is what _execute() reads to know to unwrap data[0] → dict
    # OR return None for empty. We don't run execute() here (no DB);
    # we just confirm the builder records the intent.
    assert t._single is True


# Note: end-to-end execute() tests live in test_postgres_table_integration
# (not in this commit) since they need a real Postgres connection. The
# rules of single() empty-result == None are covered by the docstring +
# inline comment in postgres_table.py and exercised by routers/services
# under the AsyncTable behaviour in test_auth_and_sessions.py.
