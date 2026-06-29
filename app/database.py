"""Database access layer.

Procta originally ran on Supabase (PostgREST). This module historically
exposed both:
  1. The sync `supabase` client (for Supabase Auth + REST queries)
  2. An `AsyncTable` REST wrapper for hot-path async queries

The async REST wrapper was removed in the postgres consolidation: every
read/write now goes through `postgres_table.PostgresTable` via the
`async_table()` factory. This eliminates the dual-adapter bug class
(string-vs-datetime, dict-vs-text, ON CONFLICT inference, etc.) that
caused real production breakage on the asyncpg backend.

What's still here:
  - `supabase`: the sync Supabase Auth client. Kept because the
    OAuth/email-verification flows in app.routers.auth still use it.
    If SUPABASE_URL is unset, a placeholder raises a clear error on
    any access. OAuth-less deployments can ignore it entirely.
  - `async_table(name)`: factory that returns a `PostgresTable`.
  - `database_backend()` / `is_postgres_backend()`: kept for callers
    that branch on backend type. Both now always return "postgres".
"""

import os
import sys
import logging
from typing import Any

_log = logging.getLogger(__name__)


def _required_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(
            f"[boot] FATAL: env var {name} is required.\n"
            "  Local dev: add to .env at repo root.\n"
            "  Docker:    set in docker-compose.yml `env_file:` or `environment:`.\n"
            "  Prod:      set in your secrets manager (KVM /etc/procta/secrets.env).",
            file=sys.stderr,
        )
        sys.exit(1)
    return v


# Procta runs exclusively on plain Postgres (asyncpg) since the
# consolidation. The env var is kept for back-compat — anything that
# explicitly sets DATABASE_BACKEND=supabase will trip the assertion
# below rather than silently revert to a now-removed code path.
_DATABASE_BACKEND = os.environ.get("DATABASE_BACKEND", "postgres").strip().lower()
if _DATABASE_BACKEND != "postgres":
    print(
        f"[boot] FATAL: DATABASE_BACKEND={_DATABASE_BACKEND!r} is no longer supported.\n"
        "  Procta runs on plain Postgres only. Unset DATABASE_BACKEND or set it to 'postgres'.",
        file=sys.stderr,
    )
    sys.exit(1)


def database_backend() -> str:
    """Returns the active backend identifier. Kept for code that reads it.
    Always 'postgres' after the consolidation."""
    return _DATABASE_BACKEND


def is_postgres_backend() -> bool:
    """Sugar around the "should I take the postgres-only path?" question.
    Always True after the consolidation — left in place so existing
    `if is_postgres_backend(): ...` blocks keep working until they're
    refactored out."""
    return True


# ─── Supabase sync client ──────────────────────────────────────────
# Only used for Supabase Auth flows (OAuth, password reset email,
# email verification token). If SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
# are both set, a real client is created. Otherwise a placeholder
# raises a clear error on any attribute access so the failure points
# at the missing env vars rather than at some downstream attribute.

class _UnavailableSupabase:
    """Placeholder used when Supabase credentials aren't configured.

    Any attribute access raises with a clear message. Callers that
    legitimately need Supabase Auth (OAuth callbacks, password reset
    email) will surface a 503-style error pointing at the missing env.
    """
    def __getattr__(self, name):
        raise RuntimeError(
            "Supabase client is not configured. Set SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY in .env to enable Supabase Auth "
            "flows (OAuth, password reset email, email verification)."
        )


_supabase_url = os.environ.get("SUPABASE_URL", "").strip()
_supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
_supabase_instance: Any = None

def get_supabase():
    """Lazy-init Supabase client — avoids blocking module import."""
    global _supabase_instance
    if _supabase_instance is None:
        if _supabase_url and _supabase_key:
            from supabase import create_client, Client
            _supabase_instance = create_client(_supabase_url, _supabase_key)
        else:
            _supabase_instance = _UnavailableSupabase()
    return _supabase_instance

def __getattr__(name):
    if name == 'supabase':
        return get_supabase()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ─── async_table factory ───────────────────────────────────────────
# Returns the PostgresTable async builder for every table. Imported as
# `async_table` (or aliased as `_atable`) by every router/service.

def async_table(name: str):
    """Create an async query builder for the given table.

    Always returns a PostgresTable that talks to plain Postgres via
    asyncpg. Imported lazily so this module stays import-safe even if
    the asyncpg pool can't connect at module load time.
    """
    from .postgres_table import postgres_table
    return postgres_table(name)
