"""Guard against the teacher-reassign bug class: a raw ``pool.acquire()`` path
that forgets to apply the RLS context.

PostgresTable.execute() applies the caller's ``app.*`` context (or the system
principal) automatically. Code that bypasses it — direct ``pool.acquire()`` +
``conn.execute`` for multi-statement transactions — MUST call
``apply_request_context`` / ``apply_to_connection`` / ``system_context`` at the
top of the transaction, or under live RLS (``procta_app``) those statements carry
NO context and silently affect 0 rows / are denied (the empty-lobby incident, and
the teacher-reassign bug this guard was born from).

This is a STATIC guard (AST, no DB needed) — it fails CI the moment a new
raw-pool path is added without context, which the RLS-less integration fixture
cannot catch.
"""
import ast
import pathlib

_APP = pathlib.Path(__file__).resolve().parent.parent / "app"

# The context-applying primitives. Any of these in the acquiring function clears it.
_CTX_CALLS = {
    "apply_request_context", "apply_to_connection",
    "system_context", "set_system_context",
}
# Infrastructure that DEFINES the primitives / the pool itself — exempt.
_EXEMPT_FILES = {"postgres_table.py", "db_context.py"}


def _functions_that_acquire(tree):
    """FunctionDef/AsyncFunctionDef nodes containing a literal `.acquire()` call."""
    funcs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "acquire"):
                    funcs.append(node)
                    break
    return funcs


def _applies_context(func_node):
    for sub in ast.walk(func_node):
        if isinstance(sub, ast.Call):
            f = sub.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if name in _CTX_CALLS:
                return True
    return False


def test_every_raw_pool_acquire_applies_rls_context():
    offenders = []
    for path in _APP.rglob("*.py"):
        if path.name in _EXEMPT_FILES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for fn in _functions_that_acquire(tree):
            if not _applies_context(fn):
                offenders.append(f"{path.relative_to(_APP.parent)}::{fn.name}()")

    assert not offenders, (
        "Raw pool.acquire() WITHOUT applying RLS context — under live RLS these "
        "statements silently affect 0 rows / are denied. Add "
        "`await db_context.apply_request_context(conn[, force_system=True])` (or a "
        "`with system_context():`) at the top of the transaction:\n  "
        + "\n  ".join(offenders))
