"""Question-bank extract → confirm persistence — against REAL Postgres.

confirm_extracted is the persistence half of the PDF/DOCX import flow: it
re-validates blocking flags + the image egress guard server-side, then writes the
reviewed questions to question_bank. This proves the real round-trip (TEXT-options
json, text[] tags) and that the server-side guards actually block — not just that
a mock returned what we fed it.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.database import async_table
from app.routers import question_bank as qb
from app.routers.question_bank import confirm_extracted, ExtractConfirmIn
from app.limiter import limiter

pytestmark = pytest.mark.asyncio

TID = "44444444-4444-4444-4444-444444444444"


async def _confirm(questions: list[dict]):
    body = ExtractConfirmIn(questions=questions)
    prev = limiter.enabled
    limiter.enabled = False
    try:
        with patch.object(qb, "require_admin", AsyncMock(return_value={"id": TID})):
            return await confirm_extracted(MagicMock(), body)
    finally:
        limiter.enabled = prev


async def _bank_rows() -> list[dict]:
    return (await async_table("question_bank").select("*")
            .eq("teacher_id", TID).execute()).data or []


async def test_confirm_persists_clean_questions():
    res = await _confirm([
        {"question": "What is 2+2?", "type": "mcq_single",
         "options": {"A": "3", "B": "4"}, "correct": "B", "tags": ["math"]},
        {"question": "Pick all primes", "type": "mcq_multi",
         "options": {"A": "2", "B": "4", "C": "3"}, "correct": "A,C", "tags": []},
    ])
    assert res["imported"] == 2

    rows = await _bank_rows()
    assert len(rows) == 2
    by_q = {r["question"]: r for r in rows}
    # options stored as a JSON string (TEXT column) and round-trips to the dict
    assert json.loads(by_q["What is 2+2?"]["options"]) == {"A": "3", "B": "4"}
    assert by_q["What is 2+2?"]["correct"] == "B"
    assert by_q["What is 2+2?"]["tags"] == ["math"]          # real text[]
    assert by_q["Pick all primes"]["question_type"] == "mcq_multi"


async def test_confirm_rejects_blocking_flag_server_side():
    # No correct answer → server recomputes `no_answer` → 400, nothing persisted
    # (defense in depth — the client's "this is clean" is never trusted).
    with pytest.raises(HTTPException) as ei:
        await _confirm([{"question": "Q", "type": "mcq_single",
                         "options": {"A": "a", "B": "b"}, "correct": ""}])
    assert ei.value.status_code == 400
    assert await _bank_rows() == []


async def test_confirm_rejects_few_options_server_side():
    # mcq with <2 options → `few_options` blocking flag → 400.
    with pytest.raises(HTTPException) as ei:
        await _confirm([{"question": "Q", "type": "mcq_single",
                         "options": {"A": "only one"}, "correct": "A"}])
    assert ei.value.status_code == 400
    assert await _bank_rows() == []


async def test_confirm_rejects_foreign_image_prefix():
    # An image_url outside the caller's own serve prefix is client tampering.
    with pytest.raises(HTTPException) as ei:
        await _confirm([{"question": "Q", "type": "mcq_single",
                         "options": {"A": "a", "B": "b"}, "correct": "A",
                         "image_url": "https://evil.example/track.png"}])
    assert ei.value.status_code == 400
    assert await _bank_rows() == []


async def test_confirm_accepts_own_tid_image_prefix():
    res = await _confirm([{"question": "Diagram Q", "type": "mcq_single",
                           "options": {"A": "a", "B": "b"}, "correct": "A",
                           "image_url": f"/api/v1/question-image/{TID}/abc123.png"}])
    assert res["imported"] == 1
    rows = await _bank_rows()
    assert rows[0]["image_url"] == f"/api/v1/question-image/{TID}/abc123.png"
