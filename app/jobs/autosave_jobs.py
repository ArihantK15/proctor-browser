"""RQ jobs for autosave persistence."""
from __future__ import annotations

from .helpers import _run_coro_in_sync
from ..services.autosave import flush_autosave_snapshot


def flush_autosave_job(session_id: str, *, delete_after: bool = False) -> dict:
    """Persist the latest Redis autosave snapshot to Supabase."""
    return _run_coro_in_sync(
        flush_autosave_snapshot(session_id, delete_after=delete_after)
    )
