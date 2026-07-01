"""Regression tests for the wrapper-following enhancement in the tenancy guards.

Both check_admin_rollup.py and check_tenant_scoping.py only inspect @router
handlers. A handler that is a thin wrapper delegating to an undecorated helper
(e.g. google_sync_roster -> _do_google_sync_roster) used to hide the helper's
real auth / own-lock / tenancy logic from the guard, letting an under-scoped
endpoint slip past (that exact pattern briefly broke the deploy gate). The
_effective_body / _called_names helpers fold same-file callees back into view.
These tests pin that behaviour.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, _SCRIPTS / f"{mod_name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


admin_rollup = _load("check_admin_rollup")
tenant_scoping = _load("check_tenant_scoping")


def _funcs(src: str) -> dict:
    tree = ast.parse(src)
    out = {}
    for f in ast.walk(tree):
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[f.name] = (f, ast.get_source_segment(src, f) or "")
    return out


WRAPPER_SRC = '''
@router.post("/sync")
async def handler(request):
    return await _do_work(body, request)

async def _do_work(body, request):
    teacher = await require_admin(request)
    tid = str(teacher["id"])
    await _atable("students").select("id").eq("teacher_id", tid).execute()
'''


@pytest.mark.parametrize("guard", [admin_rollup, tenant_scoping])
def test_called_names_extracts_bare_calls(guard):
    node = ast.parse("async def h(r):\n    return await _do_work(r)\n")
    names = guard._called_names(node)
    assert "_do_work" in names


@pytest.mark.parametrize("guard", [admin_rollup, tenant_scoping])
def test_effective_body_follows_wrapper_into_helper(guard):
    funcs = _funcs(WRAPPER_SRC)
    body = guard._effective_body("handler", funcs)
    # The wrapper alone has none of these — they live in the helper.
    assert "require_admin" in body
    assert '_atable("students")' in body
    assert '.eq("teacher_id"' in body


@pytest.mark.parametrize("guard", [admin_rollup, tenant_scoping])
def test_effective_body_ignores_unknown_callees(guard):
    # Calls to functions not defined in this file (imports/stdlib) are skipped,
    # not merged — keeps the analysis bounded to the router file's own defs.
    funcs = _funcs("async def handler(r):\n    return await external_thing(r)\n")
    body = guard._effective_body("handler", funcs)
    assert "external_thing" not in body.replace("external_thing(r)", "")  # only the call site


def test_effective_body_handles_recursion_without_hanging(guard=admin_rollup):
    funcs = _funcs("async def a(x):\n    return await b(x)\n\nasync def b(x):\n    return await a(x)\n")
    body = guard._effective_body("a", funcs)  # must terminate via the seen-set
    assert "async def a" in body and "async def b" in body


def test_real_guards_still_pass():
    """The live routers must satisfy both guards with the enhancement active."""
    assert admin_rollup.main() == 0
    assert tenant_scoping.main() == 0
