"""Database access layer.

Procta runs on plain Postgres via asyncpg. This module provides:
  - `async_table(name)`: factory that returns a `PostgresTable`.
"""

import logging
import os
import sys

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


# ─── async_table factory ───────────────────────────────────────────

def async_table(name: str):
    """Create an async query builder for the given table.

    Always returns a PostgresTable that talks to plain Postgres via
    asyncpg. Imported lazily so this module stays import-safe even if
    the asyncpg pool can't connect at module load time.
    """
    from .postgres_table import postgres_table
    return postgres_table(name)
