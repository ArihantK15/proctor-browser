"""Unit tests for app/repositories/questions.py — the data-access layer for
exam questions and exam config (28 dependents: exam.py, grading.py,
scoring.py, scorecard.py, admin_* routers, etc.).

Access-code resolution (get_access_code/set_access_code fail-closed
semantics) already has dedicated coverage in test_access_code_repository.py,
and question_type preservation / numeric-faithful ordering already has
dedicated coverage in test_load_questions_types.py — this file does not
duplicate those. Instead it covers the remaining branches in the same
module:
  - load_questions: select(*) -> narrow-select fallback chain, total
    failure (both queries raise), options dict-vs-JSON-string-vs-malformed
    parsing, `correct` decryption (success + decrypt-failure fallback),
    and Redis caching (hit short-circuits the query; empty results are
    never cached; non-empty results are cached).
  - load_exam_config: cache hit short-circuits; the "no filters at all"
    tenant-leak guard (must NOT query, must return defaults); found-row
    caches and returns the row; not-found returns defaults and does NOT
    cache a miss.
  - generate_access_code: correct length, and never emits a visually
    confusable character (I/L/1/0/O) since students copy it by hand.
  - set_access_code: all three upsert-target branches (teacher+exam,
    teacher-only, neither -> id=1) and that each invalidates the matching
    cache key.

Convention: this repo's async app code is exercised with
asyncio.new_event_loop().run_until_complete(...) inside plain `def
test_*()` functions (see test_load_questions_types.py), and `_atable(...)`
is patched with small local fluent-chain stubs rather than the shared
supabase mock (see test_scope.py's `_Rows` stub for the same pattern).
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://fake.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "fake-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")

import app.repositories.questions as Q  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _base_row(**overrides):
    row = {
        "question_id": 1,
        "question": "2+2?",
        "options": '{"A": "4", "B": "5"}',
        "correct": "A",
        "question_type": "mcq_single",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# load_questions
# ---------------------------------------------------------------------------

class _SelectChain:
    """Fluent stub for `_atable(t).select(...).eq(...).eq(...).execute()`.

    `select()` records which column list was requested so tests can tell
    the select(*) attempt apart from the narrow fallback attempt.
    `raise_on_select` lets a test make a specific select() call raise, to
    exercise load_questions' try/except fallback chain.
    """

    def __init__(self, rows=None, raise_on_select=None, exc=None):
        self._rows = rows or []
        self._raise_on_select = raise_on_select or ()
        self._exc = exc or RuntimeError("db error")
        self.eq_calls = []
        self.selected_cols = None

    def select(self, cols):
        self.selected_cols = cols
        if cols in self._raise_on_select:
            raise self._exc
        return self

    def eq(self, col, val):
        self.eq_calls.append((col, val))
        return self

    async def execute(self):
        r = MagicMock()
        r.data = self._rows
        return r


def test_load_questions_happy_path_parses_and_decrypts():
    rows = [_base_row()]
    chain = _SelectChain(rows)
    with patch.object(Q, "_atable", lambda t: chain), \
         patch.object(Q, "_cache", None), \
         patch.object(Q.secrets_crypto, "decrypt", lambda v: f"plain:{v}"):
        out = _run(Q.load_questions(teacher_id="t1", exam_id="e1"))

    assert len(out) == 1
    q = out[0]
    assert q["id"] == "1"
    assert q["options"] == {"A": "4", "B": "5"}  # JSON string was parsed
    assert q["correct"] == "plain:A"             # decrypted
    assert q["question_type"] == "mcq_single"
    # Filters were actually applied to the query, not silently dropped.
    assert ("teacher_id", "t1") in chain.eq_calls
    assert ("exam_id", "e1") in chain.eq_calls
    assert chain.selected_cols == "*"


def test_load_questions_options_dict_passthrough():
    """Supabase REST decodes jsonb -> a real dict; must not be mangled."""
    rows = [_base_row(options={"A": "4", "B": "5"})]
    chain = _SelectChain(rows)
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", None):
        out = _run(Q.load_questions())
    assert out[0]["options"] == {"A": "4", "B": "5"}


def test_load_questions_malformed_options_json_becomes_empty_dict():
    rows = [_base_row(options="{not valid json")]
    chain = _SelectChain(rows)
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", None):
        out = _run(Q.load_questions())
    assert out[0]["options"] == {}


def test_load_questions_decrypt_failure_falls_back_to_raw_value():
    """decrypt() raising must not blow up the whole load — the raw
    (possibly ciphertext) value is used as a last resort rather than
    dropping the question or crashing the caller."""
    rows = [_base_row(correct="enc:v1:garbage")]
    chain = _SelectChain(rows)
    with patch.object(Q, "_atable", lambda t: chain), \
         patch.object(Q, "_cache", None), \
         patch.object(Q.secrets_crypto, "decrypt", side_effect=RuntimeError("bad key")):
        out = _run(Q.load_questions())
    assert out[0]["correct"] == "enc:v1:garbage"


def test_load_questions_select_star_fails_falls_back_to_narrow_select():
    rows = [_base_row()]
    chain = _SelectChain(rows, raise_on_select=("*",))
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", None), \
         patch.object(Q, "_QUESTIONS_FETCH_BACKOFF", (0, 0)):
        out = _run(Q.load_questions())
    assert len(out) == 1
    assert out[0]["id"] == "1"
    # Confirms the fallback path was actually taken (narrow column list).
    assert chain.selected_cols == "question_id,question,options,correct"


def test_load_questions_both_queries_fail_raises_after_retries():
    """Regression: a real DB outage must NOT look like "this exam has zero
    questions" (the old behavior — silently returning []). See
    QuestionsFetchError's docstring and app/routers/exam.py's get_questions
    handler, which maps this to a 503 instead of the misleading 404 an
    empty list produced."""
    chain = _SelectChain(
        [_base_row()],
        raise_on_select=("*", "question_id,question,options,correct"),
    )
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", None), \
         patch.object(Q, "_QUESTIONS_FETCH_BACKOFF", (0, 0)):
        with pytest.raises(Q.QuestionsFetchError):
            _run(Q.load_questions())


def test_load_questions_transient_failure_recovers_on_retry():
    """A select() that fails once then succeeds must be recovered by the
    retry, not immediately fall through to the narrow-column fallback."""
    calls = {"n": 0}
    chain = _SelectChain([_base_row()])
    real_select = chain.select

    def flaky_select(cols):
        calls["n"] += 1
        if cols == "*" and calls["n"] == 1:
            raise RuntimeError("transient")
        return real_select(cols)

    chain.select = flaky_select
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", None), \
         patch.object(Q, "_QUESTIONS_FETCH_BACKOFF", (0, 0)):
        out = _run(Q.load_questions())
    assert len(out) == 1
    assert chain.selected_cols == "*"  # recovered on the primary select, no fallback needed


def test_load_questions_empty_result_set():
    chain = _SelectChain([])
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", None):
        out = _run(Q.load_questions())
    assert out == []


def test_load_questions_unknown_question_type_falls_back_to_mcq_single():
    rows = [_base_row(question_type="essay")]
    chain = _SelectChain(rows)
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", None):
        out = _run(Q.load_questions())
    assert out[0]["question_type"] == "mcq_single"


def test_load_questions_cache_hit_short_circuits_query():
    class _ExplodingTable:
        def select(self, *a, **kw):
            raise AssertionError("must not query the DB on a cache hit")

    cached_value = [{"id": "1", "question": "cached"}]
    fake_cache = MagicMock()
    fake_cache.get.return_value = cached_value
    with patch.object(Q, "_atable", lambda t: _ExplodingTable()), patch.object(Q, "_cache", fake_cache):
        out = _run(Q.load_questions(teacher_id="t1", exam_id="e1"))
    assert out is cached_value
    fake_cache.get.assert_called_once_with("questions:t1:e1")


def test_load_questions_nonempty_result_is_cached():
    rows = [_base_row()]
    chain = _SelectChain(rows)
    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", fake_cache):
        _run(Q.load_questions(teacher_id="t1", exam_id="e1"))
    fake_cache.set.assert_called_once()
    assert fake_cache.set.call_args.args[0] == "questions:t1:e1"


def test_load_questions_empty_result_is_never_cached():
    """`if _cache and out:` — caching an empty list would make every
    subsequent load_questions() call for this exam return [] from cache
    for the full TTL, even after questions are added."""
    chain = _SelectChain([])
    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", fake_cache):
        _run(Q.load_questions(teacher_id="t1", exam_id="e1"))
    fake_cache.set.assert_not_called()


# ---------------------------------------------------------------------------
# load_exam_config
# ---------------------------------------------------------------------------

class _ExamConfigChain:
    def __init__(self, rows):
        self._rows = rows
        self.eq_calls = []
        self.limited = False

    def select(self, *a, **kw):
        return self

    def eq(self, col, val):
        self.eq_calls.append((col, val))
        return self

    def limit(self, *a, **kw):
        self.limited = True
        return self

    async def execute(self):
        r = MagicMock()
        r.data = self._rows
        return r


def test_load_exam_config_cache_hit_short_circuits_query():
    class _ExplodingTable:
        def select(self, *a, **kw):
            raise AssertionError("must not query the DB on a cache hit")

    cached_value = {"exam_title": "Cached Exam"}
    fake_cache = MagicMock()
    fake_cache.get.return_value = cached_value
    with patch.object(Q, "_atable", lambda t: _ExplodingTable()), patch.object(Q, "_cache", fake_cache):
        out = _run(Q.load_exam_config(teacher_id="t1", exam_id="e1"))
    assert out is cached_value


def test_load_exam_config_no_filters_returns_defaults_without_executing():
    """Tenant-leak guard: with neither teacher_id nor exam_id, the query
    must never actually execute — that would .limit(1) an arbitrary row
    belonging to some other tenant. `.select()` building the query object
    is fine (it's inert), but `.limit()`/`.execute()` must not run."""
    chain = _ExamConfigChain([{"exam_title": "someone else's exam"}])

    def _exploding_limit(*a, **kw):
        raise AssertionError("must not .limit()/.execute() with no filters at all")
    chain.limit = _exploding_limit

    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", None):
        out = _run(Q.load_exam_config(teacher_id=None, exam_id=None))
    assert out["exam_title"] == "Exam"
    assert out["shuffle_questions"] is True
    assert out["shuffle_options"] is True
    assert out["pass_mark"] == 40
    assert chain.eq_calls == []


def test_load_exam_config_found_row_is_returned_and_cached():
    row = {"exam_title": "Midterm", "access_code": "AB12CD"}
    chain = _ExamConfigChain([row])
    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", fake_cache):
        out = _run(Q.load_exam_config(teacher_id="t1", exam_id="e1"))
    assert out == row
    assert ("teacher_id", "t1") in chain.eq_calls
    assert ("exam_id", "e1") in chain.eq_calls
    assert chain.limited is True
    fake_cache.set.assert_called_once_with("exam_config:t1:e1", row, ttl=300)


def test_load_exam_config_not_found_returns_defaults_and_does_not_cache():
    chain = _ExamConfigChain([])
    fake_cache = MagicMock()
    fake_cache.get.return_value = None
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", fake_cache):
        out = _run(Q.load_exam_config(teacher_id="t1", exam_id="e1"))
    assert out["exam_title"] == "Exam"
    fake_cache.set.assert_not_called()


def test_load_exam_config_teacher_only_still_queries():
    chain = _ExamConfigChain([{"exam_title": "Single-tenant"}])
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", None):
        out = _run(Q.load_exam_config(teacher_id="t1", exam_id=None))
    assert out["exam_title"] == "Single-tenant"
    assert chain.eq_calls == [("teacher_id", "t1")]


# ---------------------------------------------------------------------------
# generate_access_code
# ---------------------------------------------------------------------------

def test_generate_access_code_length_and_alphabet():
    for _ in range(200):
        code = Q.generate_access_code()
        assert len(code) == Q._ACCESS_CODE_LENGTH
        # Never contains visually-confusable characters — students copy
        # these by hand off a projector/whiteboard.
        assert not (set(code) & set("IL01O"))
        assert set(code) <= set(Q._ACCESS_CODE_ALPHABET)


def test_generate_access_code_is_not_constant():
    codes = {Q.generate_access_code() for _ in range(20)}
    assert len(codes) > 1


# ---------------------------------------------------------------------------
# set_access_code
# ---------------------------------------------------------------------------

class _UpsertChain:
    def __init__(self):
        self.payload = None

    def upsert(self, payload):
        self.payload = payload
        return self

    async def execute(self):
        return MagicMock()


def test_set_access_code_teacher_and_exam_branch():
    chain = _UpsertChain()
    fake_cache = MagicMock()
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", fake_cache):
        _run(Q.set_access_code("AB12CD", teacher_id="t1", exam_id="e1"))
    assert chain.payload == {"teacher_id": "t1", "exam_id": "e1", "access_code": "AB12CD"}
    fake_cache.delete.assert_called_once_with("exam_config:t1:e1")


def test_set_access_code_teacher_only_branch():
    chain = _UpsertChain()
    fake_cache = MagicMock()
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", fake_cache):
        _run(Q.set_access_code("AB12CD", teacher_id="t1", exam_id=None))
    assert chain.payload == {"teacher_id": "t1", "access_code": "AB12CD"}
    fake_cache.delete.assert_called_once_with("exam_config:t1:_")


def test_set_access_code_neither_branch_targets_singleton_row():
    chain = _UpsertChain()
    fake_cache = MagicMock()
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", fake_cache):
        _run(Q.set_access_code("AB12CD", teacher_id=None, exam_id=None))
    assert chain.payload == {"id": 1, "access_code": "AB12CD"}
    fake_cache.delete.assert_called_once_with("exam_config:_:_")


def test_set_access_code_no_cache_configured_does_not_raise():
    chain = _UpsertChain()
    with patch.object(Q, "_atable", lambda t: chain), patch.object(Q, "_cache", None):
        _run(Q.set_access_code("AB12CD", teacher_id="t1", exam_id="e1"))
    assert chain.payload == {"teacher_id": "t1", "exam_id": "e1", "access_code": "AB12CD"}


# ---------------------------------------------------------------------------
# _qid_sort_key (direct unit coverage; integration coverage already lives in
# test_load_questions_types.py via load_questions())
# ---------------------------------------------------------------------------

def test_qid_sort_key_numeric_before_non_numeric():
    assert Q._qid_sort_key({"id": "2"}) < Q._qid_sort_key({"id": "10"})
    assert Q._qid_sort_key({"id": "10"}) < Q._qid_sort_key({"id": "coding-abc"})


def test_qid_sort_key_non_numeric_sorts_lexically_among_itself():
    assert Q._qid_sort_key({"id": "coding-a"}) < Q._qid_sort_key({"id": "coding-b"})


def test_qid_sort_key_missing_id_treated_as_empty_non_numeric():
    # str(q.get("id", "")) -> "" which is not .isdigit() -> non-numeric bucket.
    key = Q._qid_sort_key({})
    assert key == (1, "")
