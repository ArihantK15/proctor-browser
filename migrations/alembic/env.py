"""Alembic environment configuration.

Reads DATABASE_URL from the environment. If not set, attempts to
construct one from SUPABASE_* vars (for Supabase-hosted PostgreSQL).

Usage:
    DATABASE_URL=postgresql://... alembic upgrade head
    DATABASE_URL=postgresql://... alembic revision --autogenerate -m "description"
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add project root to path so we can import app modules if needed
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Build the database URL
raw_url = os.environ.get("DATABASE_URL", "").strip()
if not raw_url:
    # Try Supabase direct connection string
    db_pass = os.environ.get("SUPABASE_DB_PASSWORD", "").strip()
    db_ref = os.environ.get("SUPABASE_PROJECT_REF", "").strip()
    if db_pass and db_ref:
        raw_url = f"postgresql://postgres:{db_pass}@db.{db_ref}.supabase.co:5432/postgres"

if raw_url:
    config.set_main_option("sqlalchemy.url", raw_url)

# Target metadata — set to None for autogenerate against live DB
# (the app uses Supabase REST, not SQLAlchemy ORM)
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generate SQL without connecting)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
