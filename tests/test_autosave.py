from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_student_token


class _FakeRedis:
    def __init__(self):
        self.values = {}
        self.deleted = []
        self.lock_acquired = True

    def pipeline(self):
        return _FakePipeline(self)

    def delete(self, *keys):
        self.deleted.extend(keys)
        for key in keys:
            self.values.pop(key, None)

    def setex(self, key, ttl, value):
        self.values[key] = value


class _FakePipeline:
    def __init__(self, client):
        self.client = client
        self.ops = []

    def setex(self, key, ttl, value):
        self.ops.append(("setex", key, ttl, value))
        return self

    def set(self, key, value, ex=None, nx=False):
        self.ops.append(("set", key, value, ex, nx))
        return self

    def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "setex":
                _, key, _ttl, value = op
                self.client.values[key] = value
                results.append(True)
            else:
                results.append(self.client.lock_acquired)
        return results


def test_cache_autosave_snapshot_writes_redis_and_lock():
    from app.services import autosave

    fake = _FakeRedis()
    with patch("app.services.autosave._cache._client", return_value=fake):
        stored, locked = autosave.cache_autosave_snapshot(
            "ALICE001_123",
            {"1": "A", 2: None},
            teacher_id="teacher-1",
            exam_id="exam-1",
        )

    assert stored is True
    assert locked is True
    assert "autosave:ALICE001_123" in fake.values
    assert '"1": "A"' in fake.values["autosave:ALICE001_123"]
    assert '"2": ""' in fake.values["autosave:ALICE001_123"]


def test_save_answers_bulk_redis_success_returns_queued(client):
    token = make_student_token(roll="ALICE001")
    with patch("app.routers.exam.cache_autosave_snapshot", return_value=(True, True)) as cache_snapshot, \
         patch("app.routers.exam._rq_enabled", return_value=True), \
         patch("app.routers.exam.enqueue_job") as enqueue, \
         patch("app.routers.exam._save_answers_bulk_to_db", new_callable=AsyncMock) as sync_save:
        resp = client.post(
            "/api/v1/save-answers-bulk",
            json={"session_id": "ALICE001_123", "answers": {"1": "A"}},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "queued", "saved": 1, "queued": True}
    cache_snapshot.assert_called_once()
    enqueue.assert_called_once()
    sync_save.assert_not_called()


def test_save_answers_bulk_redis_failure_falls_back_to_db(client):
    token = make_student_token(roll="ALICE001")
    with patch("app.routers.exam.cache_autosave_snapshot", return_value=(False, False)), \
         patch("app.routers.exam._save_answers_bulk_to_db", new_callable=AsyncMock, return_value=1) as sync_save:
        resp = client.post(
            "/api/v1/save-answers-bulk",
            json={"session_id": "ALICE001_123", "answers": {"1": "A"}},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "saved", "saved": 1, "queued": False}
    sync_save.assert_awaited_once()


def test_submit_merges_autosave_snapshot_before_scoring(client):
    token = make_student_token(roll="ALICE001")
    with patch("app.routers.exam.load_autosave_snapshot",
               return_value={"answers": {"1": "A", "2": "B"}}), \
         patch("app.routers.exam._recalculate_score", new_callable=AsyncMock, return_value=(2, 2)) as score, \
         patch("app.routers.exam._load_exam_config", new_callable=AsyncMock, return_value={"duration_minutes": 60}), \
         patch("app.routers.exam.compute_risk_score", new_callable=AsyncMock, return_value={"risk_score": 10, "label": "Low"}), \
         patch("app.routers.exam.cache_autosave_snapshot", return_value=(True, True)), \
         patch("app.routers.exam._rq_enabled", return_value=True), \
         patch("app.routers.exam.enqueue_job"), \
         patch("app.routers.exam._atable") as atable_mock:
        atable_mock.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[]))
        atable_mock.return_value.update.return_value.eq.return_value.neq.return_value.execute = AsyncMock(return_value=MagicMock(data=[{"session_key": "ALICE001_123"}]))
        atable_mock.return_value.insert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
        atable_mock.return_value.eq.return_value.eq.return_value.update.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
        atable_mock.return_value.eq.return_value.update.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))

        resp = client.post(
            "/api/v1/submit-exam",
            json={
                "session_id": "ALICE001_123",
                "roll_number": "ALICE001",
                "full_name": "Alice",
                "email": "a@test.com",
                "time_taken_secs": 600,
                "answers": {"1": "C"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert score.call_args.args[1] == {"1": "C", "2": "B"}


def test_submit_runs_final_answer_sync_fallback_when_queue_disabled(client):
    token = make_student_token(roll="ALICE001")
    with patch("app.routers.exam.load_autosave_snapshot", return_value=None), \
         patch("app.routers.exam._recalculate_score", new_callable=AsyncMock, return_value=(1, 1)), \
         patch("app.routers.exam._load_exam_config", new_callable=AsyncMock, return_value={"duration_minutes": 60}), \
         patch("app.routers.exam.compute_risk_score", new_callable=AsyncMock, return_value={"risk_score": 10, "label": "Low"}), \
         patch("app.routers.exam.cache_autosave_snapshot", return_value=(True, True)), \
         patch("app.routers.exam._rq_enabled", return_value=False), \
         patch("app.routers.exam._save_answers_bulk_to_db", new_callable=AsyncMock, return_value=1) as sync_save, \
         patch("app.routers.exam._atable") as atable_mock:
        atable_mock.return_value.select.return_value.eq.return_value.execute = AsyncMock(
            return_value=MagicMock(data=[]))
        atable_mock.return_value.update.return_value.eq.return_value.neq.return_value.execute = AsyncMock(return_value=MagicMock(data=[{"session_key": "ALICE001_123"}]))
        atable_mock.return_value.insert.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
        atable_mock.return_value.eq.return_value.eq.return_value.update.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))
        atable_mock.return_value.eq.return_value.update.return_value.execute = AsyncMock(return_value=MagicMock(data=[]))

        resp = client.post(
            "/api/v1/submit-exam",
            json={
                "session_id": "ALICE001_123",
                "roll_number": "ALICE001",
                "full_name": "Alice",
                "email": "a@test.com",
                "time_taken_secs": 600,
                "answers": {"1": "A"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    sync_save.assert_awaited_once()
