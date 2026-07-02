-- Reverse of phase150 — drop the webhook-ordering guard column.
ALTER TABLE subscriptions
  DROP COLUMN IF EXISTS last_webhook_event_at;
