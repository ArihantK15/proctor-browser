#!/usr/bin/env python3
"""Fail the build on `.select()` column syntax the postgres backend rejects.

The query builder (app/postgres_table.py:_select_list) accepts only a comma-list
of plain identifiers — or "*". PostgREST-isms the legacy Supabase REST client
tolerated raise ValueError at runtime → 500:

  • column aliases   ``.select("title:exam_title")``
  • embedded resources ``.select("exam_config(exam_title)")``

The unit suite mocks the DB, so these slip through to prod (we hit them in
api.py and lti/deeplink.py). This is a fast static guard — stdlib only, no DB.
Source of truth for the rule: postgres_table._IDENT.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"

# Mirrors app/postgres_table.py:_IDENT — a bare, safe SQL identifier.
_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _string_value(node: ast.AST) -> str | None:
    """The string value of a literal (implicit concatenation folds to one
    Constant in CPython), else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _bad_tokens(literal: str) -> list[str]:
    """Column tokens in a select string that _select_list would reject."""
    bad = []
    for col in literal.split(","):
        col = col.strip()
        if not col or col == "*":
            continue
        if not _IDENT.match(col):
            bad.append(col)
    return bad


def _module_str_consts(tree: ast.Module) -> dict[str, str]:
    """NAME -> string value for module-level `NAME = "..."` assignments, so a
    `.select(SOME_COLS)` referencing a constant is checked too (that's how the
    api.py SESSION_COLS bug hid)."""
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
           and isinstance(node.targets[0], ast.Name):
            s = _string_value(node.value)
            if s is not None:
                out[node.targets[0].id] = s
    return out


def main() -> int:
    findings: list[tuple[str, int, list[str]]] = []
    for p in sorted(APP.rglob("*.py")):
        try:
            tree = ast.parse(p.read_text(), str(p))
        except SyntaxError:
            continue
        consts = _module_str_consts(tree)
        for c in ast.walk(tree):
            if not (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                    and c.func.attr == "select" and c.args):
                continue
            a0 = c.args[0]
            literal = _string_value(a0)
            if literal is None and isinstance(a0, ast.Name):
                literal = consts.get(a0.id)
            if literal is None:
                continue
            bad = _bad_tokens(literal)
            if bad:
                findings.append((str(p.relative_to(ROOT)), getattr(c, "lineno", 0), bad))

    if findings:
        print("✗ pg-select-syntax: .select() column expressions the postgres "
              "backend rejects (→ ValueError → 500):\n")
        for f, ln, bad in sorted(findings):
            print(f"    {f}:{ln}  ->  {', '.join(repr(b) for b in bad)}")
        print("\nThe postgres builder (postgres_table._select_list) accepts only plain")
        print("identifiers or '*'. PostgREST 'alias:col' / 'related(col)' syntax raises.")
        print("Select the real columns and rename keys in Python instead.")
        return 1

    print("✓ pg-select-syntax: all .select() column lists are postgres-compatible")
    return 0


if __name__ == "__main__":
    sys.exit(main())
