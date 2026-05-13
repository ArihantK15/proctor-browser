-- Phone camera room monitoring support.
-- Adds per-exam toggle and per-session room cam state.

ALTER TABLE exam_configs ADD COLUMN IF NOT EXISTS phone_camera_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE exam_sessions ADD COLUMN IF NOT EXISTS room_cam_status TEXT NOT NULL DEFAULT 'disabled';
ALTER TABLE exam_sessions ADD COLUMN IF NOT EXISTS room_cam_approved_at TIMESTAMPTZ;
ALTER TABLE exam_sessions ADD COLUMN IF NOT EXISTS room_cam_last_frame_at TIMESTAMPTZ;
ALTER TABLE exam_sessions ADD COLUMN IF NOT EXISTS phone_camera_consented BOOLEAN NOT NULL DEFAULT FALSE;
