"""Real-filesystem tests for the screenshot retention sweep.

Screenshots live at SCREENSHOTS_DIR/{teacher_id}/{roll}/{file} — a 3-level
tree. The old sweep iterated two levels and checked is_file() on the roll
DIRECTORIES, so it deleted nothing and retention silently never ran. These
tests pin the corrected behaviour, including the S3-cache-eviction safety
(never delete a failed upload's only copy before its retention floor).
"""
import os
import time

import app.services.sessions as sessions


def _touch(path, age_days):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"jpg")
    old = time.time() - age_days * 86400
    os.utime(path, (old, old))


def test_sweep_descends_three_levels_s3_off(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SCREENSHOTS_DIR", str(tmp_path))
    monkeypatch.setattr(sessions, "_s3_enabled", lambda: False)

    old = tmp_path / "teacher-1" / "ROLL1" / "evt_old.jpg"
    new = tmp_path / "teacher-1" / "ROLL1" / "evt_new.jpg"
    _touch(old, age_days=40)   # past the 30-day floor
    _touch(new, age_days=1)

    removed = sessions._sweep_screenshots_once()

    assert removed == 1
    assert not old.exists(), "40-day-old screenshot should be deleted (retention)"
    assert new.exists(), "1-day-old screenshot must be kept"


def test_sweep_s3_on_evicts_only_when_durable_copy_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SCREENSHOTS_DIR", str(tmp_path))
    monkeypatch.setattr(sessions, "_s3_enabled", lambda: True)

    uploaded = tmp_path / "t" / "R" / "uploaded.jpg"   # 10d old, durable copy exists
    failed = tmp_path / "t" / "R" / "failed.jpg"       # 10d old, upload failed
    _touch(uploaded, age_days=10)
    _touch(failed, age_days=10)

    # head_object: present for the uploaded key, absent for the failed one.
    import app.services.object_store as ostore
    monkeypatch.setattr(ostore, "exists", lambda key: key.endswith("uploaded.jpg"))

    removed = sessions._sweep_screenshots_once()

    assert removed == 1
    assert not uploaded.exists(), "cache file with a durable S3 copy should be evicted"
    assert failed.exists(), "failed upload's only copy must NOT be deleted before the 30-day floor"


def test_sweep_s3_on_floor_deletes_failed_upload_after_30d(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SCREENSHOTS_DIR", str(tmp_path))
    monkeypatch.setattr(sessions, "_s3_enabled", lambda: True)

    stale = tmp_path / "t" / "R" / "stale.jpg"
    _touch(stale, age_days=40)

    import app.services.object_store as ostore
    monkeypatch.setattr(ostore, "exists", lambda key: False)  # never on S3

    removed = sessions._sweep_screenshots_once()

    assert removed == 1
    assert not stale.exists(), "past the 30-day floor, local is purged to bound disk"
