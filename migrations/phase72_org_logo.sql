-- Phase 72: organisation logo for white-labelled scorecards.
--
-- Adds `logo_url` column to organisations. The PDF scorecard generator
-- (app/services/scorecard.py) reads this and renders the image at the
-- top of every scorecard PDF the org issues. Filling this field is
-- what flips a customer from "Procta-branded" -> "their-brand,
-- powered by Procta" experience.
--
-- Storage is just a URL. The admin uploads to their own CDN /
-- Cloudinary / S3 / whatever and pastes the URL into Org Settings;
-- Procta does not host the asset. Keeps us out of the file-upload
-- + virus-scan + storage-quota rabbit hole.
--
-- Constraints:
--   * Up to 1024 chars (long pre-signed URLs OK)
--   * NULL allowed (free-tier customers stay un-branded)
--   * No CHECK constraint on scheme: enforced at the app layer
--     because we want to allow data: URIs in dev/sandbox without a
--     schema rewrite for prod-only validation.
--
-- Idempotent — safe to re-run.

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS logo_url TEXT;

COMMENT ON COLUMN organizations.logo_url IS
  'Optional HTTPS URL to the org logo (PNG/JPG/SVG). Rendered top-left of every scorecard PDF and at the head of branded email templates. App-layer validates scheme=https and length<=1024.';
